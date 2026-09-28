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

import csv as csvmod
import hashlib
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app import config, fixtures
from app import fx as fxmod
from app import fxlib as fxlib_mod
from app import profiles
from app import merge
from app.artnet import ArtNetSender, DMX_SLOTS
from app.sacn import SacnSender

SLOTS = DMX_SLOTS
# Safety rail for the automatic addressing loop: the flat patch grid runs
# universes 1..MAX_UNIVERSES (Art-Net's 15-bit port-address space allows
# 32768, we stop far earlier).  Explicit universe pinning (set_address)
# is not capped - only the auto-placer stops here so it can never spin.
MAX_UNIVERSES = 4096
PLAYBACK_COUNT = 10
HISTORY_LIMIT = 50
SAFE_NAME = re.compile(r"^[A-Za-z0-9 _-]{1,40}$")

# Two-phase actions: the handler manages the engine lock ITSELF, so
# act() does not hold it for the whole call.
#
# Why: the output thread needs the lock every 25 ms at 40 Hz, and a
# measured 120-head patch_from_csv held it for ~200 ms - nine dropped
# frames, a visible stall in every running fade.  These actions are split
# into a slow read-only phase (CSV parsing, SQLite mode lookups, reading a
# show file) that touches NO shared state, and a fast commit phase that
# takes the lock and mutates atomically.  The rule for adding one here:
# everything the handler mutates must happen inside `with self.lock:`.
#
# The invariant this preserves: no MUTATION happens off-lock, and nothing
# on the output thread's path (build_frames) does I/O.
SELF_LOCKED_ACTIONS = frozenset({
    "patch_from_csv", "save_show", "load_show",
})

# --- undo ------------------------------------------------------------------
# There was no undo anywhere in this codebase.  Not "not enough" - none.
# One search of app/*.py and web/*.js for "undo" returned a single hit, and
# it was a regex in the AI parser looking for the word.  For a desk that
# puts real DMX on a wire that is the worst omission in the list: a stray
# fader during a show, a mis-aimed "auto patch", a wrong delete, and none
# of it is recoverable.
#
# Design notes, each of which is a deliberate choice rather than a default:
#
#   * SNAPSHOT, not inverse commands.  An inverse per action would be less
#     memory and far more code, and it breaks the moment an action is added
#     without one - and then it breaks SILENTLY, leaving undo that undoes
#     most things and corrupts the rest.  A snapshot of the mutable state
#     cannot have that failure mode.
#
#   * Playback is NOT undoable.  Following Resolume's rule: a cue that
#     fired is an event that happened to the room, not an edit to the
#     show.  Undoing "GO" would be un-sending a transition.  So `cue_go`,
#     `blackout` and the output faders are deliberately excluded, and the
#     UI says so rather than offering a button that lies.
#
#   * Continuous edits COALESCE.  A fader drag is one intent expressed in
#     sixty calls at 22 Hz.  Without coalescing, undo would walk back
#     through the drag a frame at a time - technically an undo, useless in
#     practice.  So a repeat of the same continuous action within the
#     window replaces the top entry instead of pushing a new one.
UNDO_LIMIT = 60
UNDO_COALESCE_S = 1.2
# Actions that are an event rather than an edit: never undone.
UNDO_EXCLUDED = frozenset({
    "status", "undo", "redo", "cue_go", "cue_back", "cue_forward",
    "blackout", "master", "playback_level", "playback_activate",
    "playback_release", "set_output", "follow_set", "locate",
    # `run_command` manages its OWN undo, because a line is one step: a
    # `cue go` line must cost no Ctrl+Z at all, while `1-4 pan 90` must
    # cost exactly one.  Letting `act` push unconditionally would charge
    # for the cue line and, on failure, pop a step that was never taken.
    "run_command",
    # Changing the lock is a MODE change, not an edit to the show.  If it
    # cost an undo step then setting LOCK would push one, and the very next
    # Ctrl+Z would appear to do nothing to the show while silently
    # unlocking the desk.
    "set_lock", "unlock", "set_dry_run",
})
# Actions where a run of calls is one intent, so they collapse into a
# single step.  Only genuinely CONTINUOUS ones belong here: a value the
# operator is dragging or typing into.  Discrete edits must not coalesce -
# recording two cues 400 ms apart is two cues, and collapsing them would
# throw the first away.
UNDO_COALESCE = frozenset({
    "set_intensity", "set_attribute", "set_colour", "set_position",
    "set_address", "set_place",
})

# --- channel roles ------------------------------------------------------
# The vocabulary, the 16-bit maths and the dimmer curves live in
# engine_support so the hot path (app/merge.py) can use them without
# importing this module - a merge must never drag in the fixture
# database, the HTTP layer or the output thread.  Re-exported here
# because `from app.engine import HTP_ROLES` is the established entry
# point for the tests and the profile tooling.
from . import engine_support as _engine_support
from .engine_support import (ATTRIBUTE_ALIAS as _ATTRIBUTE_ALIAS,  # noqa: F401
                             BEAM_ROLES, COLOUR_ROLES, HTP_ROLES,
                             ROLE_HEX, ROLES, SLOTS,
                             channel_role, curve_pct as _curve_pct,
                             is_fine_role, join_16bit, logical16 as _logical16,
                             pos as _pos,
                             pos_to_ua as _pos_to_ua, split_16bit)


# Colour roles: on a fixture with no dimmer these ARE the brightness.
_COLOUR_ROLES = COLOUR_ROLES

# (manufacturer, model, mode, channels) -> (role defaults, open values),
# filled lazily by Engine._profile_levels (see app/profiles.py).
_LEVELS_CACHE: dict = {}

# "manufacturer\1fmodel" -> fixture DB row ({} = "looked, not found").
# Cached so a 120-head CSV import costs one query instead of 120; cleared
# by fixtures.invalidate_cache() whenever the DB is written.
_FIXTURE_CACHE: dict = {}

_LABEL_ROLE = _engine_support._LABEL_ROLE      # re-exported for profiles
_ZONE_RE = _engine_support._ZONE_RE

# Palette capture groups.  "beam" is the beam SHAPE (gobo, zoom, prism,
# shutter...) - brightness belongs to the programmer's level and the cue
# stack, not to a palette, so dimmer/zone_dimmer are deliberately absent:
# a beam palette on a plain RGB par would otherwise capture the level and
# then surprise the operator by re-applying it as a beam look.
PALETTE_KINDS = {
    "position": frozenset({"pan", "pan_fine", "tilt", "tilt_fine", "speed"}),
    "colour": frozenset({"red", "green", "blue", "white", "amber", "uv",
                         "cyan", "magenta", "yellow", "wheel", "macro"}),
    "beam": BEAM_ROLES,
}
# The DIMMER is deliberately in no palette kind.  A level belongs to a
# look's structure, not to its colour or its beam: capturing it in a beam
# palette and re-applying it later silently changes how bright a look is,
# which is the kind of surprise an operator cannot debug from the desk.
#
# PALETTE_KINDS is a fixed vocabulary, and that is a real limitation rather
# than an oversight: a console lets you store ANY attribute as a palette
# (grandMA's preset pools, MagicQ's four types).  "preset" below is the
# escape - a named, nestable bag of arbitrary roles - and is what a gobo,
# a beam angle or a CT filter should use until the kinds are widened.

# --- presets ---------------------------------------------------------------
# A palette is ONE attribute family ("this is House blue").  A preset is a
# COMPLETE look for a group - any subset of roles, which is what a look
# actually is.  The app had no preset concept at all, so building a look
# meant setting every head by hand and re-doing it for every group.
PRESET_ROLES = frozenset(
    set().union(*PALETTE_KINDS.values()) | {"dimmer"} | set(ROLES)
) - {"raw", "unused"}

# --- fixture profile library (offline fallback) -------------------------
# The SQLite fixture DB (app/fixtures.py) is the primary, mode-aware
# library: a fixture type's DMX footprint is the length of the channel
# list of the chosen mode (e.g. LED PAR 4ch -> 4, Moving Head Spot 16ch
# -> 16).  This table is the offline fallback for a requested type that
# is not in the DB yet, so the auto-patcher can still compute start
# addresses from a footprint.  Heads patched from here are flagged
# unverified until a real profile is imported.  First keyword match wins
# - keep the more specific keywords first.
_GENERIC_RGB = ["Red", "Green", "Blue"]                                   # 3ch
_GENERIC_PAR = ["Dimmer", "Red", "Green", "Blue"]                         # 4ch
_GENERIC_RGBW = ["Dimmer", "Red", "Green", "Blue", "White"]               # 5ch
_GENERIC_STROBE = ["Dimmer", "Strobe"]                                    # 2ch
_GENERIC_PACK = ["Dimmer"] * 8                                            # 8ch
_GENERIC_MOVING = [                                                       # 16ch
    "Pan", "Pan Fine", "Tilt", "Tilt Fine", "Pan/Tilt Speed", "Dimmer",
    "Strobe", "Red", "Green", "Blue", "White", "Gobo 1", "Gobo 1 Rotate",
    "Zoom", "Focus", "Iris"]

FALLBACK_FOOTPRINTS = (
    ("moving head", _GENERIC_MOVING), ("moving", _GENERIC_MOVING),
    ("spot", _GENERIC_MOVING), ("wash", _GENERIC_MOVING),
    ("beam", _GENERIC_MOVING),
    ("rgbw par", _GENERIC_RGBW), ("rgb par", _GENERIC_PAR),
    ("par", _GENERIC_PAR),
    ("rgbw", _GENERIC_RGBW), ("rgb", _GENERIC_RGB),
    ("bar", _GENERIC_RGBW),
    ("strobe", _GENERIC_STROBE),
    ("dimmer pack", _GENERIC_PACK), ("dimmer", _GENERIC_PACK),
    ("generic", _GENERIC_MOVING), ("test", _GENERIC_MOVING),
    ("dummy", _GENERIC_MOVING),
)


def _fallback_fixture(query: str) -> dict | None:
    """Look a fixture type up in FALLBACK_FOOTPRINTS (profile DB miss).

    Returns a synthetic fixture whose single mode carries the generic
    footprint, or None when the query names no known type - unknown
    queries keep raising, so a typo never patches the wrong shape of head.
    """
    q = str(query or "").strip().lower()
    if not q:
        return None
    for keyword, labels in FALLBACK_FOOTPRINTS:
        if keyword in q:
            return {"id": 0, "manufacturer": "Generic",
                    "model": str(query).strip(),
                    "modes": [{"name": f"Generic {len(labels)}ch",
                               "channels": list(labels)}],
                    "unverified": True}
    return None


def _attr_role(name) -> str | None:
    """Engine attribute name (or a friendly alias) -> role, or None."""
    key = re.sub(r"[\s_/]+", "", str(name or "").lower())
    if not key:
        return None
    return _ATTRIBUTE_ALIAS.get(key)


def _parse_hex(value: str) -> tuple[int, int, int]:
    s = str(value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"bad colour {value!r} - use #rrggbb")
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError as exc:
        raise ValueError(f"bad colour {value!r} - use #rrggbb") from exc


def _clamp(value, low: int, high: int) -> int:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        raise ValueError(f"not a number: {value!r}") from None
    return low if v < low else (high if v > high else v)


def _logical_to_phys(value, lo: float, hi: float, full: int = 65535) -> float:
    """A logical value as the number the fixture actually does.

    Linear in both directions is the assumption, and it is the same one
    the frame merge makes for every other role.  An INVERTED range (a
    fixture whose focus is 10..1, where DMX 0 is the far edge) falls out
    of the arithmetic for free, which is why the range is stored in the
    order the file wrote it instead of being sorted here.

    `full` is the channel's own top value - 255 for a single slot, 65535
    for a 16-bit pair.  Assuming 65535 for an 8-bit channel would put
    "90 degrees" at 4% of the travel.
    """
    span = float(hi) - float(lo)
    if abs(span) < 1e-9:
        return float(lo)
    if not full:
        full = 65535
    return float(lo) + (float(value) / float(full)) * span


def _phys_to_logical(phys: float, lo: float, hi: float,
                     full: int = 65535) -> float:
    """The logical value that lands on this physical angle."""
    span = float(hi) - float(lo)
    if abs(span) < 1e-9:
        return 0.0
    if not full:
        full = 65535
    return (float(phys) - float(lo)) / span * float(full)


def attr_domain(head: dict, role: str) -> int:
    """The programmer's top value for `role` on this head.

    THE DOMAIN IS A PROPERTY OF THE CHANNEL'S WIDTH, not a global
    constant, and getting that wrong is silent.  A level is 0-100 whether
    it lands in one slot or two, because `merge` scales it by percentage
    on the way out.  A plain channel is 0-255 in one slot and 0-65535
    across a `role`/`role_fine` pair.

    The console used 65535 everywhere, and `merge` CLAMPS an unpaired
    channel to 255 - so asking for 43690 and getting 255 on the wire was
    not a rounding detail but the entire upper half of every 8-bit channel
    being unreachable, with the encoder still showing the number you
    typed.  Any conversion that has to land on a real angle needs to know
    which of the two it is targeting.
    """
    if role in HTP_ROLES:
        return 100
    if role.endswith("_fine"):
        return 255
    return 65535 if (role + "_fine") in (head.get("map") or []) else 255


def _deg(value) -> float:
    """A physical value rounded to something an operator would read.

    0 degrees on a 234-degree tilt is logical 32767.5, which 8-bit DMX
    can only carry as 32768 — and converting that back gives
    0.00178531.  Printing the arithmetic instead of the angle makes a
    correct value look like a fault.
    """
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0


def _secrets_equal(a: str, b: str) -> bool:
    """Compare two hashes without leaking their length through timing.

    `==` on strings returns as soon as they differ, so a wrong password
    costs less time the more wrong it is - which is a real, if slow,
    way of finding a password one character at a time.
    """
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


def _round2(value) -> str:
    """A stage position for a text file: 2 dp, no trailing `.0`.

    A patch sheet is read by people, and `3.9000000000000004` in a Position
    column is noise that makes the real numbers harder to find.
    """
    try:
        text = f"{float(value or 0.0):.2f}"
    except (TypeError, ValueError):
        return "0.00"
    return text.rstrip("0").rstrip(".") or "0"


def _is_num(value) -> bool:
    """True for a bare decimal, signed or not: `90`, `-30`, `+1.5`, `.5`."""
    return bool(re.fullmatch(r"[-+]?(?:\d+\.?\d*|\.\d+)",
                             str(value or "").strip()))


def _is_int(value) -> bool:
    """True for a bare whole number, in any of the forms a console types.

    `03`, `3` and ` 3 ` all count; `3.0`, `3a` and `cue` do not.  Used to
    decide whether a token is a head number, a cue number or a word, and
    guessing that wrong is how a command line silently does the wrong
    thing instead of refusing.
    """
    if isinstance(value, int):
        return True
    return bool(re.fullmatch(r"[+-]?\d+", str(value or "").strip()))


def _num(token, what: str) -> float:
    """A number out of a typed token, or an error that says what was wanted."""
    text = str(token or "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        raise ValueError(f"{text!r} is not {what}") from None


def _similar(a: str, b: str) -> float:
    """0..1 similarity by shared prefix and shared characters.

    Crude on purpose.  It only has to answer "did they type `pann` when
    they meant `pan`", and a real edit distance would be more code than
    the feature is worth.
    """
    a, b = str(a or ""), str(b or "")
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    n = min(len(a), len(b))
    pre = 0
    while pre < n and a[pre] == b[pre]:
        pre += 1
    common = sum(1 for ch in a if ch in b)
    return (0.5 * pre / max(len(a), len(b))) + (0.5 * common / max(len(a), len(b)))


def _truthy(value) -> bool:
    """Bool from true/1/"on"/"yes" or false/0/"off"/"no"."""
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "off", "false", "no", "")
    return bool(value)


def _palette_values(raw) -> dict:
    """Normalise a palette entry's values to {role: value}.

    A palette used to be stored as a per-head snapshot -
    `{head_no: {role: value}}` - which is a PRESET wearing a palette's
    name, and it broke in three ways at once: it could not be applied to a
    fixture that did not exist when it was recorded, a re-patch orphaned
    it, and `include_palette` had to intersect the recorded heads with the
    selection (which is where the "wrote to heads nobody selected" bug
    came from).

    A console palette is ATTRIBUTE VALUES - "this is House blue" - and the
    selection decides who gets it.  So a saved entry that is still in the
    old shape is collapsed by taking, per role, the value most of the
    recorded heads agreed on.  They nearly always agree, because a palette
    is recorded from a selection the operator had just made uniform, and
    where they do not, the majority is the least surprising reading of
    "they all looked the same when I saved it".
    """
    if not isinstance(raw, dict):
        return {}
    keys = list(raw)
    if keys and all(str(k).lstrip("-").isdigit() for k in keys):
        counts: dict[str, dict] = {}
        for row in raw.values():
            if not isinstance(row, dict):
                continue
            for role, value in row.items():
                slot = counts.setdefault(str(role), {})
                slot[repr(value)] = slot.get(repr(value), 0) + 1
        out: dict[str, object] = {}
        for role, tally in counts.items():
            best = max(tally.items(), key=lambda kv: kv[1])[0]
            try:
                out[role] = json.loads(best)
            except (TypeError, ValueError):
                out[role] = best
        return out
    return {str(k): v for k, v in raw.items()}


def _copy_playbacks(playbacks) -> list[dict]:
    """A deep-enough copy of the playback list for an undo snapshot.

    Playbacks nest two levels (playback -> stack -> cue -> values), so a
    shallow `list(...)` would share the inner dicts and the "snapshot"
    would change under the undo's feet - which is the failure mode that
    makes a naive undo silently do nothing.
    """
    out = []
    for pb in playbacks or []:
        copy = dict(pb)
        copy["stack"] = [
            {**cue, "values": {int(h): dict(row)
                               for h, row in (cue.get("values") or {}).items()}}
            for cue in (pb.get("stack") or [])
        ]
        out.append(copy)
    return out


def _normalize_playbacks(saved) -> list[dict]:
    """Rebuild the playback list from saved rows (JSON keys -> int).

    Module-level, not a method, so the read phase of _a_load_show can run
    unlocked and only the swap happens under the engine lock.
    """
    playbacks = [_new_playback(i + 1) for i in range(PLAYBACK_COUNT)]
    for pb in saved or []:
        try:
            num = int(pb.get("n") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if not 1 <= num <= len(playbacks):
            continue
        base = playbacks[num - 1]
        stack = Engine._normalize_stack(pb.get("stack"))
        index = pb.get("index")
        index = -1 if index is None else int(index)
        if stack:
            index = max(-1, min(index, len(stack) - 1))
        else:
            index = -1
        base.update({"name": str(pb.get("name") or ""),
                     "stack": stack, "index": index,
                     "active": bool(pb.get("active")) and bool(stack),
                     "level": _clamp(pb.get("level", 100), 0, 100),
                     # The crossfade TIME comes back; the running crossfade
                     # does not.  Reloading a show mid-fade with a stale
                     # start value would snap the fader on load, and the
                     # operator would think the console had lost it.
                     "xfade_s": (max(0.0, float(pb["xfade_s"]))
                                 if pb.get("xfade_s") is not None else None)})
        Engine._apply_follow(base, pb)
    return playbacks


def _new_playback(n: int) -> dict:
    # follow = auto-advance of the cue stack: `at` is a monotonic
    # deadline (process-local, never saved); delay 0 means "use each
    # cue's hold time" (see _arm_follow).
    return {"n": n, "name": "", "stack": [], "index": -1, "active": False,
            "level": 100, "order": 0, "fade": None,
            "follow": {"on": False, "delay": 0.0, "paused": False,
                       "loop": False, "at": None}}


# Action allowlist.  Every name needs an _a_<name> method on Engine.
ACTIONS = (
    "add_heads", "auto_patch", "blackout", "clear_heads",
    "clear_programmer",
    "clear_selection", "cue_back", "cue_forward", "cue_go", "redo", "undo",
    "record_preset", "include_preset", "delete_preset",
    "insert_cue", "delete_cue", "move_cue", "rename_cue", "edit_cue",
    "cue_info", "set_attr_range", "remap_heads",
    "follow_set", "group_create", "group_delete", "import_scan",
    "import_show", "include_palette", "locate", "load_show", "master",
    "patch_clear", "patch_from_csv", "patch_list",
    "playback_activate", "playback_level", "playback_release",
    "record_cue", "record_palette", "remove_heads", "run_command",
    "run_fx", "save_show", "fx_available",
    "select_all", "select_group", "select_heads", "select_similar",
    "select_query", "set_address", "fan",
    "align", "distribute", "mirror", "export_patch",
    "set_limits", "clear_limits", "set_orient", "get_limits",
    "set_lock", "unlock", "set_dry_run",
    "set_attribute", "set_colour", "set_intensity", "set_output",
    "set_place", "set_position", "set_venue", "status", "stop_fx",
)


class Engine:
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
        # The room the operator drew; empty means "size it around the
        # patch" (see _a_set_venue and the visualiser).
        self.venue: dict = {}

        self.patch: list[dict] = []
        self.patch_rev = 0
        self.groups: list[dict] = []
        self.palettes: dict[str, list[dict]] = {
            "position": [], "colour": [], "beam": []}
        self.playbacks: list[dict] = [_new_playback(i + 1)
                                      for i in range(PLAYBACK_COUNT)]
        self.programmer: dict[int, dict[str, int]] = {}
        self.selected: list[int] = []
        self.fx: list[dict] = []            # running effects (see run_fx)
        self._fx_seq = 0
        self.autosave_path = Path(autosave_path) if autosave_path else None
        self._autosave_at = 0.0
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

        self._order = 0
        # Monotonic look-feed sequence (see look_feed); the visualiser
        # interpolates between ticks instead of stepping.
        self._look_seq = 0
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
        # design / operate / locked.  See LOCK_OPERATE above: a lock that
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
        self._follow_errors = 0
        self._handlers = {name: getattr(self, "_a_" + name)
                          for name in ACTIONS}
        # A fixture DB write (GDTF import, profile install) must drop our
        # cached definitions, or a re-patch would resolve modes that no
        # longer exist.
        fixtures.on_cache_clear(_FIXTURE_CACHE.clear)
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
            "mode": self.mode,
            # Per-head limits and orientation, so undo puts a fixture back
            # the way it was rigged.  A patch change is an EDIT like any
            # other, and this is the only record of it.
            "patch_extra": {h["head_no"]: {
                k: v for k, v in h.items()
                if k in ("limits", "orient")} for h in self.patch},
        }

    def _restore_state(self, state: dict) -> None:
        self.patch = [dict(h) for h in state.get("patch", [])]
        self.programmer = {int(k): dict(v) for k, v
                           in (state.get("programmer") or {}).items()}
        self.selected = set(int(h) for h in (state.get("selected") or []))
        self.groups = [dict(g) for g in (state.get("groups") or [])]
        self.palettes = {k: [dict(p) for p in v]
                         for k, v in (state.get("palettes") or {}).items()}
        self.presets = [dict(p) for p in (state.get("presets") or [])]
        self.playbacks = _copy_playbacks(state.get("playbacks") or [])
        self.venue = state.get("venue")
        self.mode = state.get("mode", self.mode)
        # Limits and orientation come back with the patch, so an undo
        # restores a fixture's rigging as well as its position.
        extra = state.get("patch_extra") or {}
        for head in self.patch:
            got = extra.get(head["head_no"]) or {}
            for key in ("limits", "orient"):
                if key in got:
                    head[key] = got[key]
                else:
                    head.pop(key, None)

    def _push_undo(self, label: str) -> None:
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
            return
        self._undo.append({"action": label, "state": self._undo_state(),
                           "at": now})
        if len(self._undo) > UNDO_LIMIT:
            del self._undo[0]
        self._redo.clear()
        self._undo_label = label
        self._redo_label = ""

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

    def _clear_undo(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._undo_label = ""
        self._redo_label = ""

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
            undoable = name not in UNDO_EXCLUDED
            if undoable:
                self._push_undo(name)
            try:
                extra = handler(**params) or {}
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                if undoable:
                    self._undo.pop()          # nothing changed
                    if self._undo:
                        self._undo_label = self._undo[-1]["action"]
                res = self._result(name, False, str(exc))
                self._log(name, False, res["error"])
                return res
            if not extra.get("ok", True) and undoable and self._undo:
                # A handler that reports its own failure without raising.
                self._undo.pop()
                if self._undo:
                    self._undo_label = self._undo[-1]["action"]
            res = self._result(name, True, None)
            if isinstance(extra, dict):
                res.update(extra)
            self._log(name, True, None, extra.get("summary"))
            if name != "status":              # read-only polls never dirty it
                self._autosave()
            self._sync_follow_thread()        # start/stop the follow ticker
            return res

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
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            with self.lock:
                res = self._result(name, False, str(exc))
                self._log(name, False, res["error"])
                return res
        with self.lock:
            res = self._result(name, True, None)
            if isinstance(extra, dict):
                res.update(extra)
            self._log(name, True, None, extra.get("summary"))
            if name != "status":
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
            if mode_name and m.get("name") == mode_name:
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
                if (m["name"].lower() == want
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
            chosen = modes[0]          # fewest channels first
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
        added = self._apply_plan(plan)
        return {"heads": added, "patched": len(self.patch),
                "summary": f"added {len(added)} x {plan[0]['model']}"}

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
            if target.is_dir() or str(path).endswith(("/", "\\")):
                # A directory gets the SHOW's name, not "patch.csv":
                # `data/audition.mrk` and `data/show.mrk` are two different
                # sheets from one rig, and calling both `patch.csv` is how
                # the wrong one gets handed to a house tech.
                first = self.patch[0] if self.patch else {}
                stem = (str(self.venue.get("name") or "").strip()
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

    def _a_select_group(self, n=None, group=None, **_):
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

    def _a_select_all(self, **_):
        self.selected = [h["head_no"] for h in self.patch]
        if not self.selected:
            raise ValueError("patch is empty")
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

    def _require_selection(self) -> list[dict]:
        if not self.selected:
            raise ValueError("nothing selected")
        wanted = set(self.selected)
        heads = [h for h in self.patch if h["head_no"] in wanted]
        if not heads:
            # A selection that no longer intersects the patch must not
            # pass silently: LOCATE/intensity would report "0 heads" and
            # look like the button is broken.
            raise ValueError("selection has no patched heads - re-patch or "
                             "re-select")
        return heads

    # --- programmer -----------------------------------------------------
    def _set_programmer(self, head_no: int, role: str, value: int) -> None:
        self.programmer.setdefault(int(head_no), {})[role] = int(value)

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
        light_from = self._profile_levels(head)[1].get(role)
        if light_from:
            return int(light_from)
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
        role = self._shutter_role(head)
        if role is None:
            return {}
        # Closed first: on the fixtures we can describe, 0 is the closed
        # end of the gate.  build_frames writes the same 0 for an
        # un-driven channel, so "nothing programmed" means "dark" - the
        # wire and the visualiser never disagree.
        if pct <= 0:
            return {role: 0}
        return {role: self._open_value(head, role)}

    def _a_set_intensity(self, level=None, fade=None, **_):
        if level is None:
            raise ValueError("level is required (0-100)")
        pct = _clamp(level, 0, 100)
        heads = self._require_selection()
        no_dimmer, driven = [], 0
        for h in heads:
            values = self._level_values(h, pct)
            if not values:
                no_dimmer.append(h["head_no"])
                continue
            for role, value in values.items():
                self._set_programmer(h["head_no"], role, value)
            driven += 1
        note = ""
        if no_dimmer:
            note = (f"; {len(no_dimmer)} head(s) have neither a dimmer nor a "
                    f"shutter channel")
        return {"level": pct, "heads": driven, "no_dimmer": no_dimmer,
                "summary": f"intensity {pct}% on {driven} head(s){note}"}


    def _a_set_attribute(self, attribute=None, value=None, **_):
        if attribute is None or value is None:
            raise ValueError("attribute and value are required")
        role = _attr_role(attribute)
        if role is None:
            raise ValueError(f"unknown attribute {attribute!r}")
        heads = self._require_selection()
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
        for h in heads:
            self._set_programmer(h["head_no"], role, raw)
        return {"attribute": role, "value": raw, "heads": len(heads),
                "summary": f"{role}={raw} on {len(heads)} head(s)"}

    def _colour_values(self, head: dict, hexcol: str) -> dict:
        r, g, b = _parse_hex(hexcol)
        roles = set(head["map"])
        out = {}
        if roles & {"red", "green", "blue"}:
            for role, v in (("red", r), ("green", g), ("blue", b)):
                if role in roles:
                    out[role] = v
        elif roles & {"cyan", "magenta", "yellow"}:
            for role, v in (("cyan", 255 - r), ("magenta", 255 - g),
                            ("yellow", 255 - b)):
                if role in roles:
                    out[role] = v
        elif "white" in roles:
            out["white"] = int(0.299 * r + 0.587 * g + 0.114 * b)
        return out

    def _white_values(self, head: dict) -> dict:
        """Values that put this head's colour at white / open."""
        roles = set(head["map"])
        out = {}
        for role in ("red", "green", "blue", "white"):
            if role in roles:
                out[role] = 255
        for role in ("cyan", "magenta", "yellow"):
            if role in roles:
                out[role] = 0
        if not out:
            # Wheel fixtures: slot 1 (DMX 0) is the open/clear slot on
            # both the colour and the gobo wheel, so 0 == "no colour,
            # full beam" - that is this fixture's white.
            for role in ("wheel", "gobo"):
                if role in roles:
                    out[role] = 0
        return out

    def _a_set_colour(self, hex=None, colour=None, value=None, **_):
        hexcol = hex or colour or value
        if not hexcol:
            raise ValueError("hex colour is required (#rrggbb)")
        _parse_hex(hexcol)                       # validate before selecting
        heads = self._require_selection()
        touched = 0
        for h in heads:
            values = self._colour_values(h, str(hexcol))
            if not values:
                continue                          # raw head: nothing to set
            for role, v in values.items():
                self._set_programmer(h["head_no"], role, v)
            touched += 1
        if not touched:
            raise ValueError("selected heads have no colour channels")
        return {"hex": str(hexcol), "heads": touched,
                "summary": f"colour {hexcol} on {touched} head(s)"}

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
                                    + f" channel (they have: "
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
                     kind=None, **_):
        """Move a head on the stage (metres, floor = 0, truss from 2 m up).

        This is the 3D view's drag handler: the operator drags a light to
        where it actually hangs and the patch follows, because "head 17 is
        at U1.113" is not what a lighting tech works with.  `kind` is
        derived from the height when not given, so dragging a light above
        2 m turns it into a truss fixture (which changes how it is drawn
        and where its beam starts).
        """
        targets: list[int] = []
        if head is not None:
            targets.append(int(head))
        targets.extend(int(h) for h in (heads or []))
        if not targets:
            raise ValueError("head is required")
        # Stage bounds.  A drag in the 3D view is a ray/plane intersection,
        # so a light far from the camera amplifies a small screen movement
        # into a large world jump - without a clamp, nudging a head
        # repeatedly walks it off to x=28 in an 18 m room and the operator
        # has no way to see it again.  Clamp to the drawn venue when there
        # is one (plus a margin, so a light can sit just off the edge),
        # otherwise to a generous default stage.
        vw = float(self.venue.get("width_m") or 0.0)
        vd = float(self.venue.get("depth_m") or 0.0)
        half_w = (vw / 2.0 + 4.0) if vw > 0 else 40.0
        max_z = (vd + 4.0) if vd > 0 else 60.0
        max_y = (float(self.venue.get("height_m") or 0.0) + 4.0) \
            if float(self.venue.get("height_m") or 0.0) > 0 else 20.0
        clamped = False
        moved = []
        for n in targets:
            h = self._head(n)                   # raises if unpatched
            before = (h["x"], h["y"], h["z"])
            if x is not None:
                h["x"] = float(x)
            if y is not None:
                h["y"] = float(y)
            if z is not None:
                h["z"] = float(z)
            cx = max(-half_w, min(half_w, h["x"]))
            cy = max(0.0, min(max_y, h["y"]))
            cz = max(0.0, min(max_z, h["z"]))
            if (cx, cy, cz) != (h["x"], h["y"], h["z"]):
                clamped = True
            h["x"], h["y"], h["z"] = cx, cy, cz
            h["kind"] = kind or ("truss" if h["y"] >= 2.0 else "floor")
            if (h["x"], h["y"], h["z"]) != before or h["kind"] != kind:
                moved.append(h["head_no"])
        if not moved:
            return {"head_no": targets[0], "heads": 0, "moved": False,
                    "clamped": clamped,
                    "x": self._head(targets[0])["x"],
                    "y": self._head(targets[0])["y"],
                    "z": self._head(targets[0])["z"],
                    "kind": self._head(targets[0])["kind"]}
        self.patch_rev += 1                 # the 3D view rebuilds on a rev
        h = self._head(moved[0])
        return {"head_no": h["head_no"], "heads": len(moved), "moved": True,
                "moved_heads": moved, "clamped": clamped,
                "x": h["x"], "y": h["y"], "z": h["z"],
                "kind": h["kind"],
                "bounds": {"half_width_m": half_w, "max_depth_m": max_z,
                           "max_height_m": max_y},
                "summary": f"moved {len(moved)} head(s) to "
                           f"x{h['x']:.2f} y{h['y']:.2f} z{h['z']:.2f}"
                           + (" (clamped to the stage)" if clamped else "")}

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

    DEFAULT_LIMITS = {"dimmer": (0, 100), "red": (0, 255), "green": (0, 255),
                      "blue": (0, 255), "white": (0, 255), "pan": (0, 255),
                      "tilt": (0, 255)}

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

    @staticmethod
    def _orient(head: dict, values: dict) -> dict:
        """Apply pan/tilt INVERT and SWAP to one head's values.

        Done here, at the frame boundary, because that is the only place
        where "the operator asked for pan 90" and "the wire needs the
        other end" can both be true.  Inverting at the programmer would
        store 165 where the operator typed 90, and every readout in the
        console would then disagree with the console's own history.
        """
        flags = head.get("orient") or {}
        swap = _truthy(flags.get("swap"))
        inv_pan = _truthy(flags.get("invert_pan"))
        inv_tilt = _truthy(flags.get("invert_tilt"))
        if not (swap or inv_pan or inv_tilt):
            return values
        out = dict(values)
        if swap:
            # Swap FIRST, then invert.  Doing it the other way round makes
            # `swap` + `invert pan` mean something different from
            # `invert pan` + `swap`, which is not a distinction any
            # operator can hold in their head while rigging a truss.
            p, t = out.get("pan"), out.get("tilt")
            if p is not None:
                out["tilt"] = p
            if t is not None:
                out["pan"] = t
        top_pan = attr_domain(head, "pan")
        top_tilt = attr_domain(head, "tilt")
        if inv_pan and "pan" in out:
            out["pan"] = top_pan - _clamp(out["pan"], 0, top_pan)
        if inv_tilt and "tilt" in out:
            out["tilt"] = top_tilt - _clamp(out["tilt"], 0, top_tilt)
        return out

    @staticmethod
    def _limit(head: dict, values: dict) -> dict:
        """Clamp one head's values to that head's own limits.

        `low` is where the lamp really goes out, so a value under it is
        sent as `low` - which for a fixture with a floor is not zero, and
        that is the point.  The STORED value is untouched: the encoder
        still reads what the operator typed, and the channel sheet shows
        what actually went out.
        """
        limits = head.get("limits")
        if not limits:
            return values
        out = dict(values)
        for role, (lo, hi) in limits.items():
            if role not in out:
                continue
            v = out[role]
            if lo is not None and v < lo:
                v = lo
            if hi is not None and v > hi:
                v = hi
            out[role] = v
        return out

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

    LOCK_OPERATE = frozenset({
        # running the show
        "cue_go", "cue_back", "cue_forward", "blackout", "master",
        "playback_level", "playback_activate", "playback_release",
        "set_intensity", "set_attribute", "set_attr_range", "set_colour",
        "set_position", "run_fx", "stop_fx", "locate", "undo", "redo",
        "follow_set", "fan", "run_command", "align", "distribute", "mirror",
        "set_venue", "set_place",
    })
    LOCK_PATCH = frozenset({
        "add_heads", "remove_heads", "patch_clear", "auto_patch",
        "set_address", "patch_from_csv", "import_scan",
        "remap_heads",
    })
    LOCK_LIBRARY = frozenset({
        "group_create", "group_delete", "record_cue", "insert_cue",
        "delete_cue", "move_cue", "rename_cue", "edit_cue", "record_palette",
        "include_palette", "record_preset", "include_preset", "delete_preset",
        "set_output", "save_show", "load_show", "import_show",
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

    def _a_set_venue(self, venue=None, width_m=None, depth_m=None,
                     height_m=None, name=None, surfaces=None, **_):
        """Store the room the operator drew (metres).

        The visualiser reads it to draw the floor, the walls and any
        truss lines, so the beams land on something real.  With nothing
        stored it falls back to a stage sized around the patch, which is
        why the view is never an empty void.
        """
        data = venue if isinstance(venue, dict) else {}
        def num(value, key, fallback=0.0):
            raw = value if value is not None else data.get(key, fallback)
            try:
                return float(raw)
            except (TypeError, ValueError):
                return fallback
        w, d = num(width_m, "width_m"), num(depth_m, "depth_m")
        hgt = num(height_m, "height_m")
        rows = surfaces if surfaces is not None else data.get("surfaces")
        clean_surfaces = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            try:
                clean_surfaces.append({
                    "kind": str(row.get("kind") or "wall")[:20],
                    "x1": float(row["x1"]), "y1": float(row["y1"]),
                    "z1": float(row["z1"]), "x2": float(row["x2"]),
                    "y2": float(row["y2"]), "z2": float(row["z2"]),
                    "color": str(row.get("color") or "#64748b")[:24],
                })
            except (KeyError, TypeError, ValueError):
                continue                     # a malformed line, not a failure
        self.venue = {"width_m": w, "depth_m": d, "height_m": hgt,
                      "name": str(name or data.get("name") or "stage")[:60],
                      "surfaces": clean_surfaces}
        self.patch_rev += 1
        return {"venue": self.venue,
                "summary": f"venue {self.venue['name']!r} "
                           f"{w:g}x{d:g} m" + (f" x {hgt:g} m high"
                                                if hgt else "")}

    def _a_locate(self, **_):
        """Show the selection: full light, white/open colour, open gobo.

        Must produce a visible change on EVERY patched head, including
        ones with no dimmer channel (their shutter/strobe opens) and
        ones whose colour lives on a wheel/gobo wheel (slot 1 = open).
        """
        heads = self._require_selection()
        no_light = []
        for h in heads:
            values = self._level_values(h, 100)
            if not values:
                # No dimmer and no shutter: all this fixture can do is
                # point at an open colour/gobo slot, so light it there.
                values = {role: 0 for role in ("wheel", "gobo")
                          if role in h["map"]}
            if not values:
                no_light.append(h["head_no"])
                continue
            values.update(self._white_values(h))
            for role, v in values.items():
                self._set_programmer(h["head_no"], role, v)
        note = ""
        if no_light:
            note = f" ({len(no_light)} head(s) have no drivable channels)"
        return {"heads": len(heads) - len(no_light), "no_light": no_light,
                "summary": f"located {len(heads) - len(no_light)} head(s){note}"}

    def _a_clear_heads(self, heads=None, **_):
        """Drop the programmer's values from specific heads only.

        `clear_programmer` empties the lot, which is right for the CLEAR
        button and wrong for a line like `1-4 clear` - a console has to be
        able to release four heads out of a selection of twenty without
        losing the other sixteen, and there was no way to ask for that.
        """
        wanted = {int(h) for h in (heads or [])}
        if not wanted:
            raise ValueError("clear needs heads: `1-4 clear`")
        touched = []
        for head_no in sorted(wanted):
            row = self.programmer.get(head_no)
            if not row:
                continue
            touched.append({"head": head_no, "roles": sorted(row)})
            self.programmer.pop(head_no, None)
        if not touched:
            raise ValueError(
                "nothing to clear on head(s) "
                + ", ".join(str(h) for h in sorted(wanted)))
        return {"heads": len(touched), "cleared": touched,
                "summary": "cleared " + ", ".join(
                    f"{t['head']} ({len(t['roles'])})" for t in touched)}

    def _a_clear_programmer(self, **_):
        n = sum(len(v) for v in self.programmer.values())
        self.programmer.clear()
        fx_n = len(self.fx)
        self.fx = []                    # CLEAR drops the effects too
        return {"cleared": n, "fx": fx_n, "summary": "programmer cleared"}

    # --- effects ---------------------------------------------------------
    def _a_fx_available(self, heads=None, group=None, **_):
        """What the named effects these fixtures can actually do.

        The UI asks this instead of filtering its own list, because a
        second filter is a second source of truth - and that is how "it
        offered me Circle and then nothing happened" happens.  This is the
        same pure function the engine uses to decide whether an effect may
        start, so the picker and the engine cannot disagree.

        A MIXED selection is reported per head and per effect: what the
        selection as a whole can do is the union, and `heads` says who.
        Offering the union while the engine quietly skips the incapable
        heads would be the same lie in a different place.
        """
        rows = self._fx_targets(heads, group)
        if not rows:
            raise ValueError("nothing selected")
        per_head = {}
        for h in rows:
            avail = fxlib_mod.available(h.get("map") or [])
            per_head[str(h["head_no"])] = avail
        union: list[str] = []
        for avail in per_head.values():
            for n in avail:
                if n not in union:
                    union.append(n)
        return {
            "heads": [h["head_no"] for h in rows],
            "available": fxlib_mod.describe(
                # the union of roles, so describe() agrees with available()
                sorted({r for h in rows for r in (h.get("map") or [])})),
            "names": sorted(union),
            "per_head": per_head,
            "summary": "%d effect(s) available on %d head(s)"
                       % (len(union), len(rows)),
        }

    def _fx_targets(self, heads=None, group=None) -> list[dict]:
        """The head rows an fx action should act on.

        Shared by run_fx and fx_available so the two can never disagree
        about what "the selection" means - the first version had the
        selection logic inline in run_fx, which meant the picker's idea of
        the selection and the engine's were two functions.
        """
        patched = {h["head_no"]: h for h in self.patch}
        if heads is not None:
            wanted: list[int] = []
            for h in heads:
                n = int(h)
                if n not in patched:
                    raise ValueError(f"head {n} is not patched")
                if n not in wanted:
                    wanted.append(n)
        elif group is not None:
            num_g = int(group)
            row = next((g for g in self.groups if g["n"] == num_g), None)
            if row is None:
                raise ValueError(f"no group {num_g}")
            wanted = [n for n in row["heads"] if n in patched]
        else:
            wanted = [h["head_no"] for h in self._require_selection()]
        return [patched[n] for n in wanted]

    def _a_run_fx(self, attribute=None, wave=None, kind=None, speed=1.0,
                  spread=0.0, phase=0.0, base=None, depth=None,
                  duration=None, heads=None, group=None, name=None,
                  params=None, **_):
        """Start a running effect on the selection (or explicit heads/group).

        TWO KINDS, ONE ACTION.

        With `name=`, one of the library effects in `app/fxlib.py` - Rainbow,
        Circle, Fan, Gobo spin - and it writes SEVERAL roles per head, so
        it needs no `attribute` and the old single-role arguments are
        ignored.  Without it, the original LFO: one wave on one attribute,
        which is the right primitive and is what the library is built on.

        A named effect is offered only to a fixture that can do it, and on a
        MIXED selection the incapable heads are SKIPPED and named in the
        result, rather than the whole thing being refused: selecting four
        PARs and two movers and asking for Circle should run Circle on the
        movers, not do nothing because two of the six are PARs.  Refusing
        outright is the other defensible choice and it is the wrong one -
        it makes a mixed selection, which is the normal case on a rig, a
        thing you cannot run an effect on.
        """
        if name not in (None, ""):
            return self._a_run_fx_named(
                str(name).lower(), params, duration, heads, group)

        def num(value, default=0.0) -> float:
            if value in (None, ""):
                return float(default)
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ValueError(f"not a number: {value!r}") from None

        role = _attr_role(attribute)
        if role is None or role not in ROLES or role == "unused":
            raise ValueError(
                "run_fx needs an attribute (dimmer, pan, tilt, red, ...)")
        name = str(wave if wave not in (None, "") else kind or "sine").lower()
        if name not in fxmod.WAVES:
            raise ValueError(f"unknown wave {name!r} - use one of "
                             f"{', '.join(fxmod.WAVES)}")
        hz = max(fxmod.SPEED_MIN, min(fxmod.SPEED_MAX, num(speed, 1.0)))
        spread_deg = max(-fxmod.SPREAD_MAX,
                         min(fxmod.SPREAD_MAX, num(spread, 0.0)))

        if heads is not None:
            patched = {h["head_no"] for h in self.patch}
            wanted: list[int] = []
            for h in heads:
                n = int(h)
                if n not in patched:
                    raise ValueError(f"head {n} is not patched")
                if n not in wanted:
                    wanted.append(n)
        elif group is not None:
            num_g = int(group)
            row = next((g for g in self.groups if g["n"] == num_g), None)
            if row is None:
                raise ValueError(f"no group {num_g}")
            patched = {h["head_no"] for h in self.patch}
            wanted = [n for n in row["heads"] if n in patched]
        else:
            wanted = [h["head_no"] for h in self._require_selection()]
        if not wanted:
            raise ValueError("nothing selected")
        if len(self.fx) >= 16:
            raise ValueError("too many effects running - stop some first")

        low, high = (0, 100) if role in HTP_ROLES else (0, 255)
        base_v = low if base in (None, "") else _clamp(base, low, high)
        depth_v = (high - low if depth in (None, "")
                   else _clamp(depth, 0, high - low))
        dur = None
        if duration not in (None, "", 0, "0"):
            d = num(duration, 0.0)
            dur = min(d, 86400.0) if d > 0 else None

        self._fx_seq += 1
        row = {"id": self._fx_seq, "role": role, "kind": name,
               "speed": hz, "spread": spread_deg,
               "phase": num(phase, 0.0) / 360.0,    # degrees -> cycles
               "base": base_v, "depth": depth_v, "low": low, "high": high,
               "heads": list(wanted), "t0": time.monotonic(),
               "duration": dur}
        self.fx.append(row)
        return {"fx": row["id"], "heads": len(wanted),
                "summary": f"{name} {role} fx on {len(wanted)} head(s)"}

    def _a_run_fx_named(self, name: str, params, duration, heads,
                         group) -> dict:
        """Start one of the `app/fxlib.py` effects.

        The capability decision is made HERE, with the same pure function
        the picker uses, and the heads that cannot do it are dropped and
        reported.  That is the whole contract: an effect runs only where it
        can, and the operator is told where it did not.
        """
        if name not in fxlib_mod.FX:
            raise ValueError(
                "unknown effect %r - one of %s"
                % (name, ", ".join(sorted(fxlib_mod.FX))))
        rows = self._fx_targets(heads, group)
        if not rows:
            raise ValueError("nothing selected")
        capable, skipped = [], []
        for h in rows:
            avail = fxlib_mod.available(h.get("map") or [])
            (capable if name in avail else skipped).append(h)
        if not capable:
            # Nothing here can do it.  The reason is per head, because the
            # usual cause is a mode with no dimmer or no colour, and "needs
            # pan" is useless if the fixture has no pan channel to lack.
            why = "; ".join(
                "head %d: %s" % (h["head_no"],
                                 fxlib_mod.why_not(h.get("map") or [], name))
                for h in skipped[:4])
            raise ValueError(
                "%s cannot run here - %s" % (
                    fxlib_mod.FX[name]["label"],
                    why + ("" if len(skipped) <= 4 else " (+%d more)"
                           % (len(skipped) - 4))))
        if len(self.fx) >= 16:
            raise ValueError("too many effects running - stop some first")
        p = fxlib_mod.defaults(name)
        if isinstance(params, dict):
            for k, v in params.items():
                if k in p and v is not None:
                    try:
                        p[k] = float(v)
                    except (TypeError, ValueError):
                        raise ValueError(
                            "not a number for %s: %r" % (k, v)) from None
        dur = None
        if duration not in (None, "", 0, "0"):
            try:
                d = float(duration)
            except (TypeError, ValueError):
                raise ValueError("not a number: %r" % (duration,)) from None
            dur = min(d, 86400.0) if d > 0 else None

        self._fx_seq += 1
        row = {"id": self._fx_seq, "lib": name, "params": p,
               "heads": [h["head_no"] for h in capable],
               "t0": time.monotonic(), "duration": dur}
        self.fx.append(row)
        msg = "%s on %d head(s)" % (fxlib_mod.FX[name]["label"],
                                   len(capable))
        if skipped:
            msg += " (skipped %d: %s)" % (
                len(skipped), ", ".join(str(h["head_no"]) for h in skipped[:6]))
        return {"fx": row["id"], "effect": name, "heads": len(capable),
                "skipped": [h["head_no"] for h in skipped],
                "summary": msg}

    def _a_stop_fx(self, id=None, fx=None, **_):
        target = id if id is not None else fx
        if target is None:
            n = len(self.fx)
            self.fx = []
            return {"stopped": n, "summary": f"stopped {n} effect(s)"}
        try:
            tid = int(target)
        except (TypeError, ValueError):
            raise ValueError(f"bad effect id: {target!r}") from None
        before = len(self.fx)
        self.fx = [r for r in self.fx if r["id"] != tid]
        if len(self.fx) == before:
            raise ValueError(f"no effect {tid} running")
        return {"stopped": 1, "fx": tid, "summary": f"stopped effect {tid}"}

    def _fx_values(self, now: float | None = None) -> dict[int, dict[str, int]]:
        """{head_no: {role: value}} for every running, unexpired effect.

        Called from the frame builder and the look feed (both under the
        lock); expired effects are dropped here, so no other bookkeeping
        is needed.
        """
        if not self.fx:
            return {}
        now = time.monotonic() if now is None else now
        out: dict[int, dict[str, int]] = {}
        keep: list[dict] = []
        for row in self.fx:
            elapsed = now - row["t0"]
            dur = row.get("duration")
            if dur is not None and elapsed >= dur:
                continue                              # expired - drop it
            keep.append(row)
            heads = row["heads"]
            count = len(heads)
            if row.get("lib"):
                # A NAMED effect writes SEVERAL roles per head, so the
                # per-head dict is UPDATED rather than assigned.  The
                # index passed in is the head's position among the heads
                # this effect actually runs on - which is the running set,
                # not the original selection - so a spread across two movers
                # out of a mixed six spans those two, not every sixth of the
                # original selection.
                by_no = {h["head_no"]: h for h in self.patch}
                for i, head_no in enumerate(heads):
                    roles = by_no.get(head_no, {}).get("map") or []
                    try:
                        vals = fxlib_mod.apply(
                            row["lib"], {}, roles, params=row.get("params"),
                            elapsed=elapsed, index=i, count=count)
                    except ValueError:
                        # The patch changed under a running effect - a mode
                        # was re-imported and the head lost the channel.  Drop
                        # the effect rather than throwing inside the frame
                        # builder, which would take the whole DMX tick with
                        # it.  Silently, because there is nobody to tell at
                        # 200 Hz; the effect stops being listed.
                        continue
                    if vals:
                        out.setdefault(head_no, {}).update(vals)
                continue
            for i, head_no in enumerate(heads):
                cycles = fxmod.cycles_for(elapsed, row["speed"], row["phase"],
                                          row["spread"], i, count)
                value = fxmod.fx_value(row["kind"], cycles, row["base"],
                                       row["depth"],
                                       seed=row["id"] * 1000 + head_no,
                                       low=row["low"], high=row["high"])
                out.setdefault(head_no, {})[row["role"]] = value
        if len(keep) != len(self.fx):
            self.fx = keep
        return out

    # --- palettes --------------------------------------------------------
    def _a_record_palette(self, kind=None, name="", palette=None, **_):
        key = str(kind or "").strip().lower()
        if key not in PALETTE_KINDS:
            raise ValueError(f"kind must be one of {sorted(PALETTE_KINDS)}")
        heads = self._require_selection()
        attrs = PALETTE_KINDS[key]
        # ONE value set, not a per-head row.  Every selected head normally
        # agrees (a palette is recorded from a selection the operator has
        # just made uniform), and where they do not, the most common value
        # is the honest reading - and the operator is told, because
        # silently averaging a colour nobody asked for is worse than a
        # clear message.
        votes: dict[str, dict] = {}
        disagree = 0
        for h in heads:
            row = {k: v for k, v in self.programmer.get(h["head_no"],
                                                        {}).items()
                   if k in attrs}
            if not row:
                continue
            for role, value in row.items():
                votes.setdefault(role, {}).setdefault(repr(value), 0)
                votes[role][repr(value)] += 1
        if not votes:
            raise ValueError(f"programmer holds no {key} values")
        values: dict[str, object] = {}
        for role, tally in votes.items():
            top = max(tally.values())
            if top < sum(tally.values()):
                disagree += 1
            try:
                values[role] = json.loads(
                    max(tally.items(), key=lambda kv: kv[1])[0])
            except (TypeError, ValueError):
                continue
        if not values:
            raise ValueError(f"programmer holds no {key} values")
        n = int(palette) if palette else len(self.palettes[key]) + 1
        label = str(name).strip() or f"{key.title()} {n}"
        entry = {"n": n, "name": label, "values": values}
        for i, old in enumerate(self.palettes[key]):
            if old["n"] == n:
                self.palettes[key][i] = entry
                break
        else:
            self.palettes[key].append(entry)
        note = (f" ({disagree} value(s) differed across the selection - "
                f"kept the most common)" if disagree else "")
        return {"kind": key, "n": n, "name": label, "disagree": disagree,
                "roles": sorted(values),
                "summary": f"recorded {key} palette {label}{note}"}

    def _a_include_palette(self, kind=None, n=None, palette=None, **_):
        key = str(kind or "").strip().lower()
        if key not in PALETTE_KINDS:
            raise ValueError(f"kind must be one of {sorted(PALETTE_KINDS)}")
        num = int(n if n is not None else palette or 0)
        entry = next((p for p in self.palettes[key] if p["n"] == num), None)
        if entry is None:
            raise ValueError(f"no {key} palette {num}")
        # The SELECTION decides who gets it - there is no longer a list of
        # heads baked into the entry, so the bug this replaces (a disjoint
        # selection falling through and writing to the recorded heads) is
        # not possible to express any more, rather than merely guarded.
        heads = self._require_selection()
        applied, skipped = 0, []
        for h in heads:
            row = {r: v for r, v in (entry.get("values") or {}).items()
                   if r in (h.get("map") or [])}
            if not row:
                skipped.append(h["head_no"])
                continue
            self.programmer.setdefault(h["head_no"], {}).update(row)
            applied += 1
        if not applied:
            raise ValueError(
                f"{entry['name']} has nothing for the selected head(s) - "
                f"they have none of: {', '.join(sorted(entry.get('values') or {}))}")
        return {"kind": key, "n": num, "heads": applied,
                "skipped": skipped,
                "summary": (f"included {key} {entry['name']} on {applied} head(s)"
                            + (f"; {len(skipped)} had no matching channel"
                               if skipped else ""))}

    # --- presets ----------------------------------------------------------
    def _a_record_preset(self, name="", preset=None, **_):
        """Store the SELECTION's whole programmer as a named look.

        A palette is one attribute family; a preset is a complete look -
        colour and beam and position and level together - which is what
        you actually build with, and which the app could not express at
        all.  Re-recording an existing number overwrites it, so a look can
        be revised in place.
        """
        heads = self._require_selection()
        attrs: dict[str, int] = {}
        for h in heads:
            for role in (h.get("map") or []):
                attrs[role] = attrs.get(role, 0) + 1
        values: dict[str, object] = {}
        used: list[int] = []
        for h in heads:
            row = {r: v for r, v in (self.programmer.get(h["head_no"]) or {}).items()
                   if r in PRESET_ROLES}
            if not row:
                continue
            used.append(h["head_no"])
            for role, value in row.items():
                votes = values.setdefault(role, {})
                votes.setdefault(repr(value), 0)
                votes[repr(value)] += 1
        if not values:
            raise ValueError("programmer is empty - set something first")
        collapsed: dict[str, object] = {}
        for role, tally in values.items():
            try:
                collapsed[role] = json.loads(
                    max(tally.items(), key=lambda kv: kv[1])[0])
            except (TypeError, ValueError):
                continue
        n = int(preset) if preset else len(self.presets) + 1
        label = str(name).strip() or f"Preset {n}"
        entry = {"n": n, "name": label, "values": collapsed,
                 "heads": len(used)}
        for i, old in enumerate(self.presets):
            if old["n"] == n:
                self.presets[i] = entry
                break
        else:
            self.presets.append(entry)
        return {"n": n, "name": label, "heads": len(used),
                "roles": sorted(collapsed),
                "summary": f"recorded preset {label} "
                           f"({len(used)} head(s), {len(collapsed)} attribute(s))"}

    def _a_include_preset(self, n=None, preset=None, **_):
        num = int(n if n is not None else preset or 0)
        entry = next((p for p in self.presets if p["n"] == num), None)
        if entry is None:
            raise ValueError(f"no preset {num}")
        heads = self._require_selection()
        applied, skipped = 0, []
        for h in heads:
            row = {r: v for r, v in (entry.get("values") or {}).items()
                   if r in (h.get("map") or [])}
            if not row:
                skipped.append(h["head_no"])
                continue
            self.programmer.setdefault(h["head_no"], {}).update(row)
            applied += 1
        if not applied:
            raise ValueError(
                f"{entry['name']} has nothing for the selected head(s)")
        return {"n": num, "name": entry["name"], "heads": applied,
                "skipped": skipped,
                "summary": f"applied preset {entry['name']} to {applied} head(s)"
                           + (f"; {len(skipped)} had no matching channel"
                              if skipped else "")}

    def _a_delete_preset(self, n=None, preset=None, **_):
        num = int(n if n is not None else preset or 0)
        before = len(self.presets)
        self.presets = [p for p in self.presets if p["n"] != num]
        if len(self.presets) == before:
            raise ValueError(f"no preset {num}")
        return {"n": num, "summary": f"deleted preset {num}"}

    # --- playbacks / cues -------------------------------------------------
    def _playback(self, playback) -> dict:
        num = int(playback)
        if not 1 <= num <= len(self.playbacks):
            raise ValueError(f"playback must be 1..{len(self.playbacks)}")
        return self.playbacks[num - 1]

    def _a_record_cue(self, playback=None, name="", fade=None, hold=None,
                      cue=None, follow=None, **_):
        pb = self._playback(playback if playback is not None else 1)
        if not self.programmer:
            raise ValueError("programmer is empty - set something first")
        values = {h: dict(row) for h, row in self.programmer.items() if row}
        if not values:
            raise ValueError("programmer is empty - set something first")
        cue_n = int(cue) if cue else len(pb["stack"]) + 1
        if cue_n < 1:
            raise ValueError("cue numbers start at 1")
        entry = {"n": cue_n, "name": str(name).strip() or f"Cue {cue_n}",
                 "fade_s": float(fade if fade is not None else 0.0),
                 "hold_s": float(hold if hold is not None else 0.0),
                 "values": values}
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
        self.programmer.clear()
        return {"playback": pb["n"], "cue": cue_n, "cues": len(pb["stack"]),
                "summary": f"recorded cue {cue_n} on PB{pb['n']}"}

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
        # Inherits, like a recorded cue - see record_cue.  An inserted slot
        # with no opinion of its own is the right answer: a "record me here"
        # marker that then refuses to be stepped through would be worse than
        # useless, and one that always auto-fires would be worse still.
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

    def _a_edit_cue(self, playback=None, cue=None, fade=None, hold=None,
                    name=None, follow=None, **_):
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
        # follow=null CLEARS the cue's own value, so it goes back to
        # inheriting the stack default.  That has to be expressible, or a cue
        # pinned to "wait" can never be released once the operator changes
        # their mind.  `_` is the leftover kwargs bag, so "follow" in it
        # means the caller MENTIONED follow even when the value is null -
        # the only way to tell "set it to wait" from "never mentioned it".
        if "follow" in _:
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
        target = self._scaled_cue(cue, at)
        pb["fade"] = {"t0": now,
                      "dur": float(cue.get("fade_s") or 0.0), "from": was}
        # The target is kept, not re-derived: after the fade has finished
        # `_pb_values` used to return `cue["values"]` directly, which would
        # have snapped the rig back to full the instant the fade ended.
        pb["target"] = target
        pb["at"] = at
        pb["index"] = index
        pb["active"] = True
        self._order += 1
        pb["order"] = self._order
        self._arm_follow(pb, now)              # manual steps re-arm here
        return {"playback": pb["n"], "cue": cue["n"], "name": cue["name"],
                "cues": len(stack), "active": True, "fade_s": cue["fade_s"],
                "at": at}

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
                self._follow_errors += 1
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

    # --- output / safety ---------------------------------------------------
    def _a_status(self, **_):
        return {"mode": self.mode, "dry_run": self.dry_run,
                "live": self.live, "patched": len(self.patch),
                "selected": len(self.selected), "blackout": self.blackout,
                "master": self.master, "output": self._output_public(),
                # The lock, so the agent and the API can see it without
                # guessing: an agent that tries to record a cue into a
                # locked desk should say so rather than discovering it by
                # the refusal.
                "lock": self.lock_state,
                "lock_has_password": bool(getattr(self, "_lock_hash", "")),
                "following": [pb["n"] for pb in self.playbacks
                              if pb["follow"]["on"]],
                "active_playbacks": [pb["n"] for pb in self.playbacks
                                     if pb["active"]]}

    def _a_set_output(self, state=None, confirm=False, **_):
        if state is None:
            raise ValueError("state is required")
        want = state in (True, 1, "1", "true", "on", "start")
        if want:
            if not self.dry_run and not confirm:
                raise ValueError("real DMX output needs confirm:true")
            self.live = True
            self._start_output()
            return {"live": True, "dry_run": self.dry_run,
                    "summary": "output started" +
                               (" (dry run)" if self.dry_run else " - LIVE")}
        self.live = False
        self._stop_output()
        return {"live": False, "summary": "output stopped"}

    def _a_set_dry_run(self, state=None, confirm=False, **_):
        """Turn dry run on and off WHILE THE DESK IS RUNNING.

        Until this existed the only way out of dry run was to edit .env
        and restart - which is exactly the thing that stops someone
        testing on a real rig, and the reason `dry_run` was still true the
        first time somebody plugged a node in.

        The sender is built ONCE with `dry_run` baked in, so this reaches
        into it rather than rebuilding: replacing the sender mid-show
        would drop the first frame and re-open a socket, and the operator
        would see the rig blink for a reason nobody could name.

        Turning dry run OFF while the output is LIVE needs `confirm`,
        because that is the moment the desk starts driving real fixtures.
        Turning it ON is always allowed - that is the safe direction, and
        it is the one you want in a hurry.
        """
        want = _truthy(state) if state is not None else not self.dry_run
        if not want and self.live and not confirm:
            raise ValueError(
                "turning dry run off while the output is LIVE starts "
                "driving real fixtures — pass confirm:true to do it")
        was = self.dry_run
        self.dry_run = bool(want)
        # The sender holds its own copy, and it is the thing that decides
        # whether a socket is touched at all.
        if self._sender is not None:
            self._sender.dry_run = self.dry_run
        self._log("dry_run", True, None,
                  "dry run on" if self.dry_run else "dry run OFF")
        self._autosave()
        return {"dry_run": self.dry_run, "live": self.live,
                "was": was,
                "summary": ("DRY RUN — frames are built and counted, "
                            "nothing leaves this machine"
                            if self.dry_run else
                            "LIVE — frames are going to the network"
                            + ("" if self.live else
                               " (press GO LIVE to start sending)"))}

    def _a_blackout(self, state=1, **_):
        if isinstance(state, str):
            self.blackout = state.strip().lower() not in (
                "0", "off", "false", "no", "release")
        else:
            self.blackout = bool(int(state or 0))
        return {"blackout": self.blackout,
                "summary": "blackout on" if self.blackout else
                           "blackout released"}

    def _a_master(self, level=None, **_):
        if level is None:
            raise ValueError("level is required (0-100)")
        self.master = _clamp(level, 0, 100)
        return {"master": self.master}

    def _start_output(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        thread = threading.Thread(target=self._run, name="jarvis-dmx",
                                  daemon=True)
        self._thread = thread
        thread.start()

    def _stop_output(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and \
                thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        self.output["running"] = False

    def shutdown(self) -> None:
        """Process exit: stop the threads, optionally send blackout."""
        self.live = False
        try:
            self._autosave(force=True)     # never lose the last edit
        except Exception:
            pass
        self._stop_output()
        self._stop_follow_thread()
        self._stop_writer()               # flush any queued autosave
        if config.DMX_BLACKOUT_ON_EXIT and not self.dry_run:
            try:
                sender = self._get_sender()
                black = {u: bytearray(SLOTS) for u in self._universes()}
                for universe, buf in black.items():
                    for _ in range(3):
                        sender.send(universe, buf)
            except Exception:
                pass
        if self._sender is not None:
            self._sender.close()

    def _get_sender(self) -> ArtNetSender | SacnSender:
        """Lazy sender for the configured transport (Art-Net or sACN).

        Both classes expose the same interface (send/stats/close/dry_run
        and the frame counters), so patch, merge, FX and frame logic
        never change with the wire protocol - only _dispatch's socket
        target does.
        """
        if self._sender is None:
            if config.DMX_TRANSPORT == "sacn":
                self._sender = SacnSender(
                    config.DMX_HOST, config.DMX_PORT, config.DMX_NET,
                    self.dry_run, priority=config.SACN_PRIORITY,
                    source_name=config.SACN_SOURCE_NAME,
                    cid=config.SACN_CID or None)
            else:
                self._sender = ArtNetSender(config.DMX_HOST, config.DMX_PORT,
                                            config.DMX_NET, self.dry_run)
        return self._sender

    def _run(self) -> None:
        """Output thread: one frame per tick, deadline scheduled."""
        period = 1.0 / float(config.DMX_HZ)
        period_ema_ms = period * 1000.0
        target = time.monotonic()
        previous = None
        self.output["running"] = True
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now < target:
                    self._stop.wait(min(0.004, target - now))
                    continue
                try:
                    with self.lock:
                        frames = self.build_frames()
                    self._dispatch(frames)
                except Exception as exc:            # never die silently
                    self.output["errors"] += 1
                    self.output["last_error"] = str(exc)
                done = time.monotonic()
                if previous is not None:
                    inst_ms = (done - previous) * 1000
                    self.output["last_tick_age_ms"] = round(inst_ms, 1)
                    # smooth the PERIOD, then invert: averaging rates biases
                    # high whenever jitter makes some ticks short
                    period_ema_ms = period_ema_ms * 0.85 + inst_ms * 0.15
                    self.output["hz"] = round(1000.0 / period_ema_ms, 1)
                self.output["drift_ms"] = round(
                    max(0.0, done - target) * 1000, 1)
                previous = done
                target += period
                if target < done - period:
                    target = done           # fell behind: resync, no burst
        finally:
            self.output["running"] = False

    def _dispatch(self, frames: dict) -> None:
        sender = self._get_sender()
        for universe, buf in frames.items():
            if sender.dry_run:
                self.output["simulated_frames"] += 1
                continue
            if sender.send(universe, buf):
                self.output["frames_sent"] += 1
            else:
                self.output["errors"] += 1
                self.output["last_error"] = sender.last_error

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
        if not fade:
            return target
        dur = float(fade.get("dur") or 0.0)
        if dur <= 0:
            return target
        t = (now - float(fade["t0"])) / dur
        if t >= 1.0:
            return target
        if t <= 0:
            return fade["from"]
        src, dst = fade["from"], target
        out = {}
        for head_no in set(src) | set(dst):
            a, b = src.get(head_no) or {}, dst.get(head_no) or {}
            row = {}
            for attr in set(a) | set(b):
                v0, v1 = a.get(attr, 0), b.get(attr, 0)
                row[attr] = int(round(v0 + (v1 - v0) * t))
            out[head_no] = row
        return out

    def _htp_value(values: dict, role: str):
        """Compatibility shim - the merge lives in app/merge.py."""
        return merge.htp_value(values, role)

    def _resolve_head(self, head: dict, prog: dict,
                      pb_vals: list[tuple[int, dict]],
                      fx_row: dict[str, int] | None = None) -> dict:
        """Final per-role values for one head, through the shared merge.

        Delegated to app/merge.py (see that module for the precedence
        rules) so the wire and the visualiser can never drift apart:
        both call the same function.
        """
        return merge.resolve_head(head, prog, pb_vals, fx_row,
                                  self.master, self.blackout)

    def build_frames(self, now: float | None = None) -> dict[int, bytearray]:
        """Merge programmer + playbacks + effects into 512-byte frames.

        The hard real-time path: it runs every 25 ms at 40 Hz while HTTP
        threads mutate the patch.  The arithmetic therefore lives in
        app/merge.py as pure functions with no I/O and no state, which
        makes it benchmarkable and testable on its own; this method only
        snapshots the state it needs and hands it over.
        """
        now = time.monotonic() if now is None else now
        return merge.build_frames(self.patch, self.programmer,
                                  self._active_playbacks(now),
                                  self._fx_values(now),
                                  self.master, self.blackout)

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
    # -- the command line ---------------------------------------------
    # ------------------------------------------------------------------
    #
    # THE LAST PIECE OF THE OPERATOR LOOP.  Every control the console has
    # can be reached with the mouse, which is fine until you want to do
    # the same thing to a second group of heads, or you know the fixture
    # is 90 degrees out and you do not want to drag an encoder to find out
    # which way.  A command line is not a scripting gimmick: on grandMA,
    # MagicQ and Eos it is the FASTEST way to do ordinary work, and
    # operators build muscle memory for it.
    #
    # It lives in the engine, not the client, for one reason: there would
    # otherwise be two parsers - one in JavaScript for the console and one
    # for the agent and the HTTP API - and they would disagree about
    # exactly the ambiguous cases.  `1-4 pan 90` has to mean one thing.
    #
    # A LINE IS ONE UNDO STEP AND IS ALL-OR-NOTHING.  Typing
    # `1-4 pan 90 red 255` and having pan apply when red fails leaves the
    # rig in a state nobody asked for and cannot predict from the
    # transcript.  So the line is parsed before anything is touched, and
    # if any step fails the whole line is rolled back to where it started.
    # A `cue go` line pushes NO undo step at all, because firing a cue is
    # an event rather than an edit - the same reason cue_go is excluded
    # from the button path.
    #
    # A bare number followed by `go` is a CUE, not a head.  `1-4 go` would
    # be one head and go to nothing; `3 go` firing cue 3 is what the
    # operator meant, and a console that made them say `cue 3 go` every
    # time would be typed at and sworn at.

    # `aim` / `at` / `position` are three spellings of one verb, so they
    # are named once here and folded into CMD_VERBS below.  A console's own
    # vocabulary is `At`; `aim` is the plain-English one.
    CMD_AIM = ("aim", "at", "position")
    CMD_VERBS = frozenset({
        "go", "back", "cue", "group", "palette", "preset", "master",
        "blackout", "clear", "home", "record", "thru", "off", "help",
        "fan", "select", "store", "align", "distribute", "mirror",
    } | set(CMD_AIM))
    CMD_KEYWORDS = ("all", "none", "off", "full", "on")
    # What may BEGIN a line.  A verb needs no selection of its own; a
    # selection keyword IS one.  Getting this wrong is why `all` answered
    # "nothing is selected" on an empty rig - it was skipped as a keyword
    # and then never read as the selection it is.
    CMD_START = CMD_VERBS | {"all", "none", "*"}

    def _cmd_help(self) -> str:
        return (
            "selection    1-4   1.3.5   all   none   *   group 3\n"
            "attributes   1-4 pan 90   1-4 dimmer 50   1-4 red 255\n"
            "             1-4 tilt off   1-4 wheel full   1-4 dimmer +10\n"
            "cues         go   back   cue 3 go   cue 3 at 50   3 at 50\n"
            "recording    record   record 2   1-4 clear   clear\n"
            "looks        palette colour 2   preset 1\n"
            "aiming       1-4 aim pan 90   1-4 aim 90 -30   1-4 at 90\n"
            "arranging    align x   distribute z   mirror x   mirror x about 0\n"
            "desk         master 60   master full   blackout on\n"
            "A value of `off` REMOVES the attribute; `+N`/`-N` move it by.\n"
            "`pan 90` is 90 DEGREES on a fixture whose file says its travel;\n"
            "with no range known it is a plain 0-255 value.\n"
            "align puts every head on ONE line; distribute spaces them\n"
            "EVENLY between the two outermost; mirror flips the shape.")

    @staticmethod
    def _cmd_tokens(text: str) -> list[str]:
        return [t for t in str(text or "").replace(",", " ").split() if t]

    def _cmd_selection(self, tokens: list[str], i: int) -> tuple[set, int]:
        """Read a leading selection, or return the current one.

        Returns the heads to act on and the token index reached, AND
        whether the operator actually named heads in this line.

        The flag matters because the alternative - "did it find any heads" -
        cannot tell `1-4` (a real selection) from `group 3` naming a group
        that is now empty, and silently acting on the current selection
        instead is how a command line fires the wrong thing.

        A selection is a RANGE (`1-4`), a LIST (`1.3.5`, which is how every
        console spells "heads 1, 3 and 5" without commas), `all`, `none`,
        `*`, or `group N`.
        """
        if i >= len(tokens):
            return (set(self.selected), i, False)
        tok = tokens[i].lower()
        if tok in ("all", "*"):
            if not self.patch:
                raise ValueError("nothing is patched yet")
            return ({h["head_no"] for h in self.patch}, i + 1, True)
        if tok in ("none", "home"):
            return (set(), i + 1, True)
        if tok == "group":
            num = tokens[i + 1] if i + 1 < len(tokens) else None
            if num is None:
                raise ValueError("group needs a number: `group 3`")
            if not _is_int(num):
                raise ValueError(f"group {num!r} is not a number")
            g = next((x for x in self.groups if int(x["n"]) == int(num)), None)
            if g is None:
                have = ", ".join(str(x["n"]) for x in self.groups) or "none"
                raise ValueError(f"no group {num} - you have {have}")
            return ({int(h) for h in g["heads"]}, i + 2, True)
        head = re.fullmatch(r"(\d+(?:\.\d+)*)(?:-(\d+))?", tok)
        if head:
            if head.group(2):
                lo, hi = sorted((int(head.group(1)), int(head.group(2))))
                wanted = set(range(lo, hi + 1))
            else:
                wanted = {int(p) for p in head.group(1).split(".")}
            # Name the head numbers that do not exist, COUNTED rather than
            # listed.  `1-999` on a 5-head rig printing 994 numbers tells
            # the operator nothing they can act on and buries the message.
            patched = {h["head_no"] for h in self.patch}
            missing = sorted(wanted - patched)
            if missing:
                if len(missing) > 8:
                    raise ValueError(
                        f"heads not patched: {missing[0]}-{missing[-1]} "
                        f"({len(missing)} of them; this rig has heads "
                        f"{min(patched)}-{max(patched)})")
                raise ValueError("heads not patched: "
                                 + ", ".join(str(m) for m in missing))
            return (wanted, i + 1, True)
        return (set(self.selected), i, False)

    def _cmd_value(self, tok: str, role: str) -> tuple[str, float | None]:
        """`off`, `full`, `+N`, `-N` and plain numbers.

        Relative values are here because "these are 10% brighter" is a
        thing operators say out loud, and it is the one adjustment a
        console cannot do for you by dragging a fader to an absolute
        position.
        """
        text = str(tok or "").strip().lower()
        if text in ("off", "0%") and text == "off":
            return ("off", None)
        if text in ("full", "100%", "on"):
            return ("full", None)
        if text[0] in "+-":
            try:
                return ("add", float(text))
            except ValueError:
                raise ValueError(f"{text!r} needs a number after the sign"
                                 ) from None
        try:
            return ("set", float(text))
        except ValueError:
            raise ValueError(
                f"{tok!r} is not a value - use a number, off, full, "
                f"or +N / -N to move by") from None

    def _a_run_command(self, text=None, command=None, dry=False, **_):
        """Run one typed line.  See the note above the verb table."""
        line = str(text if text is not None else command or "").strip()
        if not line:
            return {"transcript": [], "summary": "nothing typed",
                    "ok": True, "steps": []}
        if line.startswith("?") or line.lower() in ("help", "?"):
            return {"help": self._cmd_help(), "transcript": [],
                    "steps": [], "ok": True,
                    "summary": "command syntax"}
        # A trailing `.` is the console terminator and is not a token.
        tokens = self._cmd_tokens(line)
        while tokens and tokens[-1] == ".":
            tokens.pop()
        if not tokens:
            return {"transcript": [], "steps": [], "ok": True,
                    "summary": "nothing typed"}

        # ---- PARSE.  Nothing below this line mutates until PLAN is built.
        plan: list[tuple[str, dict]] = []
        note_lines: list[str] = []
        i = 0
        heads: set = set()

        first = tokens[0].lower()
        # A bare cue number: `3 go` is a cue, because one head cannot go.
        cue_shortcut = False
        if re.fullmatch(r"\d+", first) and len(tokens) >= 2 \
                and tokens[1].lower() in ("go", "at", "back"):
            cue_shortcut = True
        # `all`, `none` and `*` ARE selections, so they go through the
        # reader.  `group N` too - but a bare `group` with no number has to
        # reach the verb section to say "group needs a number", because
        # otherwise the failure reads as "incomplete" and sends the
        # operator looking for a syntax problem that is not there.
        if first == "group" and len(tokens) == 1:
            raise ValueError("group needs a number: `group 3`")
        named_sel = False
        if not cue_shortcut and first not in self.CMD_START:
            heads, i, named_sel = self._cmd_selection(tokens, 0)
        elif first in ("all", "none", "*"):
            heads, i, named_sel = self._cmd_selection(tokens, 0)
        elif first == "group" and len(tokens) > 1:
            heads, i, named_sel = self._cmd_selection(tokens, 0)
        else:
            heads, i = set(self.selected), 0

        if i >= len(tokens):
            if named_sel and not cue_shortcut:
                # `1-4` or `all` on its own is a selection, and a useful
                # one.  `none` on its own means deselect all.
                plan.append(("select_heads", {"heads": sorted(heads)}))
                return self._cmd_finish(line, plan, note_lines, dry)
            raise ValueError("incomplete: " + self._cmd_help())

        tok = tokens[i].lower()
        # A selection NAMED in this line always applies.  Without this,
        # `1-2 tilt off` on two heads that have no tilt would fall through
        # to "nothing is selected" and quietly do something else - the
        # named heads are gone from `heads` only because they lack the
        # channel, which is not the same as not having been asked for.
        if named_sel:
            plan.append(("select_heads", {"heads": sorted(heads)}))
        if not heads and not cue_shortcut and tok not in (
                "go", "back", "master", "blackout", "record", "clear",
                "home", "thru", "help", "select"):
            raise ValueError(
                "nothing to act on - no patched head matches"
                if named_sel else "nothing is selected - name some heads first")

        # ---- selection verbs ------------------------------------------
        if tok in ("home", "none", "*"):
            if not plan:
                plan.append(("select_heads", {"heads": []}))
            return self._cmd_finish(line, plan, note_lines, dry)
        if tok == "clear" and len(tokens) == i + 1:
            plan.append(("clear_programmer", {}))
            return self._cmd_finish(line, plan, note_lines, dry)
        if tok in ("record", "store"):
            if len(tokens) == i + 2 and _is_int(tokens[i + 1]):
                plan.append(("record_cue", {"cue": int(tokens[i + 1])}))
            else:
                plan.append(("record_cue", {}))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- cues -------------------------------------------------------
        if tok == "cue" or cue_shortcut:
            if cue_shortcut:
                num, i = int(first), 1
            else:
                if i + 1 >= len(tokens) or not _is_int(tokens[i + 1]):
                    raise ValueError("cue needs a number: `cue 3 go`")
                num, i = int(tokens[i + 1]), i + 2
            at = None
            if i < len(tokens) and tokens[i].lower() == "at":
                if i + 1 >= len(tokens):
                    raise ValueError("`cue 3 at` needs a percentage")
                at = _num(tokens[i + 1], "a percentage")
                i += 2
            params_cue: dict = {"cue": num}
            if at is not None:
                params_cue["at"] = at
            plan.append(("cue_go", params_cue))
            return self._cmd_finish(line, plan, note_lines, dry)
        if tok in ("go", "back"):
            plan.append(("cue_go" if tok == "go" else "cue_back", {}))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- looks ------------------------------------------------------
        if tok in ("palette", "preset"):
            if tok == "preset":
                if i + 1 >= len(tokens) or not _is_int(tokens[i + 1]):
                    raise ValueError("preset needs a number: `preset 1`")
                plan.append(("include_preset", {"n": int(tokens[i + 1])}))
                return self._cmd_finish(line, plan, note_lines, dry)
            # A palette is per attribute FAMILY, so `palette 2` is genuinely
            # ambiguous.  It is allowed when exactly one family has a
            # palette 2, which is the usual case on a small rig, and
            # refused with the families listed when it is not.
            nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
            if _is_int(nxt):
                num = int(nxt)
                have = [k for k in PALETTE_KINDS
                        if any(p["n"] == num for p in self.palettes.get(k, []))]
                if len(have) == 1:
                    plan.append(("include_palette", {"kind": have[0],
                                                     "n": num}))
                    return self._cmd_finish(line, plan, note_lines, dry)
                if not have:
                    raise ValueError(
                        f"no palette {num} - you have "
                        + (", ".join(f"{k} "
                                     + ",".join(str(p['n']) for p
                                                in self.palettes.get(k, []))
                                     for k in PALETTE_KINDS
                                     if self.palettes.get(k)) or "none"))
                raise ValueError(
                    f"palette {num} exists in {len(have)} families "
                    f"({', '.join(have)}) - say which: "
                    f"`palette {have[0]} {num}`")
            if nxt not in PALETTE_KINDS:
                raise ValueError(
                    f"palette needs a family and a number: `palette colour 2`"
                    f" - families are {', '.join(sorted(PALETTE_KINDS))}")
            if i + 2 >= len(tokens) or not _is_int(tokens[i + 2]):
                raise ValueError(f"palette {nxt} needs a number: "
                                 f"`palette {nxt} 2`")
            plan.append(("include_palette", {"kind": nxt,
                                             "n": int(tokens[i + 2])}))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- aim: `1-4 aim pan 90 tilt -30`, or `1-4 at 90,128` -----------
        # Degrees when the fixture knows its travel, logical otherwise -
        # and the server decides which, not this parser, because only it
        # knows the fixture.  So both readings are sent and the transcript
        # says which one was used.
        if tok in self.CMD_AIM or (
                len(tokens) == i + 3 and re.fullmatch(r"[-+]?[\d.]+",
                                                      tokens[i + 1] or "")
                and re.fullmatch(r"[-+]?[\d.]+", tokens[i + 2] or "")):
            pos: dict = {}
            i += 1
            # Words and bare numbers both work, and the bare form is
            # positional: `aim 90 -30` is pan then tilt, because that is
            # the order every console prints them in.
            role_order: list[str] = []
            while i < len(tokens) and len(role_order) < 2:
                word = tokens[i]
                low = word.lower()
                if low in ("pan", "tilt"):
                    if i + 1 >= len(tokens):
                        raise ValueError(f"{low} needs a value")
                    pos[low] = _num(tokens[i + 1], "a value")
                    role_order.append(low)
                    i += 2
                    continue
                if _is_num(word):
                    slot = role_order[0] if role_order else None
                    if slot is None:
                        pos["pan"] = _num(word, "a value")
                        role_order.append("pan")
                    elif slot == "pan" and "tilt" not in pos:
                        pos["tilt"] = _num(word, "a value")
                        role_order.append("tilt")
                    else:
                        break
                    i += 1
                    continue
                break
            if not pos:
                raise ValueError(
                    "aim needs a pan and/or tilt: `1-4 aim pan 90`, "
                    "`1-4 aim 90 -30` (pan then tilt)")
            if i < len(tokens):
                raise ValueError(
                    f"unexpected {tokens[i]!r} - aim takes pan and tilt only")
            if named_sel:
                plan.append(("select_heads", {"heads": sorted(heads)}))
            # `unit` is NOT sent.  Only the server knows whether this
            # fixture's travel is in degrees, and a client-side guess would
            # be exactly the silent reinterpretation §17.19 exists to
            # prevent.  The transcript says which reading was used.
            plan.append(("set_position", dict(pos)))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- arranging on stage -----------------------------------------
        # The same three words every console uses, and the distinction
        # between them is the whole point: ALIGN puts every head on one
        # line, DISTRIBUTE spaces them evenly between the two outermost,
        # MIRROR flips the shape.  A row of six with the middle two
        # bunched is not misaligned, so aligning is the wrong tool and
        # treating the two as synonyms is how these features go wrong.
        if tok in ("align", "distribute", "mirror"):
            action = {"align": "align", "distribute": "distribute",
                      "mirror": "mirror"}[tok]
            params_arr: dict = {}
            if named_sel:
                params_arr["heads"] = sorted(heads)
            axis_tok = tokens[i + 1] if i + 1 < len(tokens) else "x"
            params_arr["axis"] = axis_tok
            nxt = i + 2
            if action == "mirror" and nxt < len(tokens) \
                    and tokens[nxt].lower() == "about":
                if nxt + 1 >= len(tokens):
                    raise ValueError("`mirror x about` needs a value")
                params_arr["about"] = _num(tokens[nxt + 1],
                                           "a position in metres")
                nxt += 2
            elif nxt < len(tokens) and _is_int(tokens[nxt]):
                # A bare number after the axis is the centre for mirror,
                # and is meaningless for align/distribute - which do not
                # take a centre, so say so rather than ignoring it.
                if action == "mirror":
                    params_arr["about"] = _num(tokens[nxt], "a position")
                else:
                    raise ValueError(
                        f"{tok} does not take a number - it works on the "
                        f"selection as it stands "
                        f"(to centre a mirror, use `mirror x about N`)")
                nxt += 1
            if nxt < len(tokens):
                raise ValueError(f"unexpected {tokens[nxt]!r}")
            plan.append((action, params_arr))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- desk ------------------------------------------------------
        if tok == "master":
            word = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
            plan.append(("master", {"level":
                            100.0 if word in ("full", "on", "100")
                            else (0.0 if word in ("off", "0")
                                  else _num(word, "a level 0-100"))}))
            return self._cmd_finish(line, plan, note_lines, dry)
        if tok == "blackout":
            word = tokens[i + 1].lower() if i + 1 < len(tokens) else "toggle"
            if word not in ("on", "off", "toggle"):
                raise ValueError("blackout takes on, off, or nothing")
            plan.append(("blackout", {"state":
                            (not self.blackout) if word == "toggle"
                            else int(word == "on")}))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- `1.3 clear` / `1-4 off` : clear the programmer on heads -----
        if len(tokens) == i + 1 and tok == "clear":
            plan.append(("clear_heads", {"heads": sorted(heads)}))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- attributes -------------------------------------------------
        want = re.sub(r"[^a-z0-9]", "", tok)
        role = _attr_role(tok)
        if role is None:
            near = self._cmd_near(tok, list(_ATTRIBUTE_ALIAS))
            raise ValueError(
                f"do not know {tok!r}" + (f" - did you mean {near}?"
                                          if near else "")
                + "\n" + self._cmd_help())
        if i + 1 >= len(tokens):
            raise ValueError(f"{role} needs a value: `{role} 0`, "
                             f"`{role} off`, `{role} +10`")
        leftover = tokens[i + 2:]
        if leftover:
            # A console takes a trailing `.` as the terminator - `1-4 pan 90 .`
            # is normal - but anything else is a second command typed without
            # separating it, and guessing which to run would be worse than
            # saying so.
            raise ValueError(
                f"unexpected {leftover[0]!r} after the value - one command "
                f"per line (press Enter between them)")
        how, val = self._cmd_value(tokens[i + 1], role)
        rng = self.role_range([h for h in self.patch
                               if h["head_no"] in heads], role)
        params: dict = {"attribute": role}
        if how == "off":
            # `off` REMOVES the attribute rather than setting it to zero.
            # They are not the same thing: a palette value underneath shows
            # through a removed attribute and is overwritten by a zero, so
            # `off` has to be the one that means "let go of it".
            #
            # An attribute that was never set is a NO-OP, not an error.
            # `1-2 tilt off` where neither head has a tilt in the programmer
            # is the operator tidying up, and answering "it was not set" is
            # the sort of pedantry that teaches people not to type.
            if not any((self.programmer.get(h["head_no"]) or {}).get(role)
                       for h in self.patch if h["head_no"] in heads):
                note_lines.append(f"{role} was not set — nothing to remove")
                plan.append(("select_heads", {"heads": sorted(heads)}))
                return self._cmd_finish(line, plan, note_lines, dry)
            params["clear"] = True
        elif how == "full":
            params["value"] = 100 if role in HTP_ROLES else (
                min([attr_domain(h, role) for h in self.patch
                     if h["head_no"] in heads] or [255]))
        elif how == "add":
            params["value"] = val
            params["relative"] = True
            if rng.get("unit") == "degree":
                params["unit"] = "degree"
        else:
            params["value"] = val
            if rng.get("unit") == "degree" and val is not None \
                    and abs(val) <= 360:
                # `1-4 pan 90` means 90 DEGREES on a head that says its
                # travel is in degrees.  The unit is still sent explicitly
                # rather than inferred here - the server does the deciding
                # and the transcript says which reading was used.
                params["unit"] = "degree"
        plan.append(("set_attr_range", params))
        return self._cmd_finish(line, plan, note_lines, dry)

    @staticmethod
    def _cmd_near(word: str, pool: list[str]) -> str:
        """The closest known word, if one is close enough to be a typo."""
        w = re.sub(r"[^a-z0-9]", "", str(word or "").lower())
        best, score = "", 0.0
        for cand in pool:
            s = _similar(w, re.sub(r"[^a-z0-9]", "", cand))
            if s > score:
                best, score = cand, s
        return best if score >= 0.6 else ""

    def _cmd_finish(self, line: str, plan: list, notes: list,
                    dry: bool) -> dict:
        """Execute a parsed plan as ONE atomic, ONE-undo-step change."""
        mutating = [p for p in plan if p[0] not in UNDO_EXCLUDED]
        if dry:
            return {"text": line, "steps": [{"action": a, "params": p}
                                            for a, p in plan],
                    "transcript": [], "ok": True, "dry": True,
                    "summary": f"would run {len(plan)} step(s)",
                    "help": self._cmd_help()}
        before = self._undo_state() if mutating else None
        transcript = []
        # The interesting fields of each step (`clipped`, `partial`,
        # `missing`, ...) are surfaced so a client can MARK the heads
        # involved rather than re-reading a sentence.  Later steps win,
        # since the last thing a line did is what the operator is looking
        # at.
        extra: dict = {}
        try:
            for action, params in plan:
                handler = self._handlers.get(action)
                if handler is None:
                    raise ValueError(f"unknown action {action!r}")
                res = handler(**params) or {}
                if res.get("ok") is False:
                    raise ValueError(res.get("error")
                                     or f"{action} failed")
                said = res.get("summary")
                if said:
                    transcript.append(said)
                extra.update({k: v for k, v in res.items()
                              if k not in ("ok", "summary", "action",
                                           "engine", "simulated", "error")})
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            # A line either does what it says or nothing.  Rolling back
            # rather than leaving the first half applied is what makes the
            # transcript trustworthy.
            #
            # Selection is restored too, and deliberately: `1-2 dimmer 50`
            # changes WHICH HEADS are selected as a side effect, and a
            # failed line that quietly left the selection moved would mean
            # the next line acts on heads nobody chose.
            if before is not None:
                self._restore_state(before)
            raise ValueError(
                f"{exc} — nothing from this line was applied"
                if mutating else str(exc)) from None
        if mutating:
            # The state BEFORE the line, pushed as ONE step, and labelled
            # with the line itself so the undo button reads
            # "undo: 1-4 pan 90" rather than "undo".  The label is the
            # whole point of that button - a button that might undo
            # something is worse than no button.
            self._undo.append({"action": "command", "label": line,
                               "state": before, "at": self._clock()})
            if len(self._undo) > UNDO_LIMIT:
                del self._undo[0]
            self._undo_label = line
            self._redo.clear()
            self._redo_label = ""
            self._autosave()
        self._sync_follow_thread()
        # The vocabulary the client's tab-completion offers, so it can never
        # suggest a command this parser does not have.  It arrives with
        # every run, so a change here shows up in the box immediately.
        return {
            "text": line,
            "steps": [{"action": a, "params": p} for a, p in plan],
            "transcript": transcript,
            "notes": notes,
            "ok": True,
            "selection": sorted(self.selected),
            **extra,
            "cmd_verbs": sorted(self.CMD_VERBS),
            "summary": (" · ".join(transcript) or line) + (
                "   [" + " · ".join(notes) + "]" if notes else ""),
        }

    def _a_set_attr_range(self, attribute=None, role=None, value=None,
                          unit=None, clear=False, relative=False, **_):
        """Set one attribute across a selection, reporting partial writes.

        This is what an encoder row calls.  It exists rather than reusing
        `set_attribute` because an encoder's promise is "this knob now
        says X" and `set_attribute` silently did nothing to a head that
        lacks the channel - so a fader row would show a value that only
        some of the rig received, with nothing on screen to say so.

        `unit="degree"` converts the value through the FIXTURE'S OWN
        range.  Without it an operator aiming a moving head types 18000
        and has to know that means 0 degrees on a 540-degree pan and
        nothing at all on the next light in the row.  The unit is
        EXPLICIT: with no `unit` the number is logical (0-100 for a level,
        0-65535 otherwise), which is what every other caller means.
        """
        want = str(attribute or role or "").strip().lower()
        resolved = _attr_role(want)
        if resolved is None:
            raise ValueError(f"unknown attribute {want!r}")
        if value is None and not (clear or relative):
            raise ValueError("a value is required")
        try:
            asked = 0.0 if value is None else float(value)
        except (TypeError, ValueError):
            raise ValueError(f"not a number: {value!r}") from None
        heads = self._require_selection()
        capable = [h for h in heads if resolved in (h.get("map") or [])]
        if not capable:
            have = sorted({r for h in heads for r in (h.get("map") or [])
                           if r not in ("raw", "unused")})
            raise ValueError(
                f"none of the selected heads has a {resolved} channel "
                f"(they have: {', '.join(have) or 'nothing controllable'})")
        full = min([attr_domain(h, resolved) for h in capable] or [255])

        # --- `off`: REMOVE the attribute -----------------------------------
        # Not "set it to zero".  A zero is a value that overrides whatever
        # the palette or playback underneath was doing, whereas removing it
        # lets that show through again - which is what a console's OFF
        # means, and the difference is visible the moment anything else is
        # driving the same heads.
        if clear:
            removed, untouched = [], []
            for h in capable:
                row = self.programmer.get(h["head_no"])
                if row and resolved in row:
                    row.pop(resolved, None)
                    removed.append(h["head_no"])
                    if not row:
                        self.programmer.pop(h["head_no"], None)
                else:
                    untouched.append(h["head_no"])
            if not removed:
                raise ValueError(
                    f"{resolved} was not set on "
                    + (", ".join(str(h['head_no']) for h in capable)
                       or "any selected head"))
            return {"attribute": resolved, "cleared": removed,
                    "heads": len(capable), "no_op": untouched,
                    "summary": (f"{resolved} removed from {len(removed)} of "
                                f"{len(capable)} head(s)")}

        # --- physical units ------------------------------------------------
        # A role in HTP_ROLES is a 0-100 LEVEL and never has a physical
        # range; asking for degrees on one is a mistake worth naming
        # rather than silently clamping.
        #
        # `unit` is EXPLICIT or the value is logical.  Guessing is the trap
        # here: "90" means 90 of 65535 to a script and 90 DEGREES to someone
        # looking at a field labelled °, and silently picking one of those
        # is how a head ends up pointing at the wall.  The client is told
        # the unit by `attribute_state` and passes it back.
        want_unit = str(unit or "").strip().lower()
        phys = want_unit in ("degree", "deg", "degrees")
        phys_note = ""
        # The domain of the NARROWEST capable channel.  A 16-bit pan pair
        # takes 0-65535 and an 8-bit one 0-255; sending 0-65535 to a
        # mixed selection clamps every 8-bit head to full and reports
        # success, which is the worst of both.
        full = min([attr_domain(h, resolved) for h in capable] or [255])
        rng = self.role_range(capable, resolved)
        if phys:
            if rng.get("mixed"):
                # Only blocks the CONVERSION.  A logical value means the
                # same thing on every head whatever the light's travel, so
                # refusing that would take away the one number that still
                # works on a mixed selection.
                raise ValueError(
                    f"the selected {resolved} channels have different ranges "
                    f"({'; '.join('%g..%g' % (lo, hi) for lo, hi in rng['mixed'])})"
                    f" — set them one at a time, or give a value in "
                    f"0-65535")
            if not rng:
                raise ValueError(
                    f"no range is known for {resolved} on this fixture, so "
                    f"degrees cannot be converted — give a 0"
                    f"{'-100' if resolved in HTP_ROLES else '-65535'} value")
            if rng.get("unit") != "degree":
                raise ValueError(
                    f"{resolved} is not measured in degrees on this fixture "
                    f"(it is a {rng.get('unit', 'raw')} value)")
            value = _phys_to_logical(asked, rng["min"], rng["max"], full)
        elif want_unit and rng.get("unit") == "degree" \
                and want_unit not in ("auto", "logical", "raw", "dmx"):
            raise ValueError(
                f"{resolved} is measured in degrees, not {want_unit}")

        # --- `+N` / `-N`: move by, per head -------------------------------
        # Each head moves from ITS OWN current value, so a mixed selection
        # that is already uneven stays uneven in the same proportions.  One
        # "old value" taken from the first head would quietly level them,
        # which is the opposite of what "these are 10% brighter" means.
        if relative:
            step = asked
            if phys:
                # A relative ANGLE: convert the offset through the same
                # range, so `tilt +10` is ten degrees on this light rather
                # than ten thousandths of its travel.
                step = _phys_to_logical(step, rng["min"], rng["max"], full)
            landed, clipped = [], []
            for h in capable:
                top = 100 if resolved in HTP_ROLES else attr_domain(h, resolved)
                was = float((self.programmer.get(h["head_no"]) or {})
                            .get(resolved, 0) or 0)
                new = _clamp(was + step, 0, top)
                # "Clipped" means the step COULD NOT BE TAKEN IN FULL, in
                # either direction.  Only checking `step > 0` misses a fall
                # that hit the bottom, which is the half an operator meets
                # first: `dimmer -100` from 30 stores 0 and must say so,
                # because the alternative reading is "you asked for -100
                # and the desk did that".
                if abs(new - (was + step)) > 0.5:
                    clipped.append(h["head_no"])
                landed.append(new)
                if resolved in HTP_ROLES:
                    for r, v in self._level_values(h, new).items():
                        self._set_programmer(h["head_no"], r, v)
                else:
                    self._set_programmer(h["head_no"], resolved, new)
            missing = [h["head_no"] for h in heads
                       if resolved not in (h.get("map") or [])]
            note = ""
            if missing:
                note += (f"; {len(missing)} head(s) have no {resolved} "
                         f"channel and were left alone")
            if clipped:
                note += ("; " + ", ".join(str(c) for c in clipped)
                         + (" hit" if len(clipped) == 1 else " hit")
                         + " the end and could not move "
                         + ("as far" if len(clipped) == 1 else "as far"))
            note += f" by {_deg(asked):g}°" if phys else f" by {step:+g}"
            return {"attribute": resolved,
                    "value": landed[0] if landed else None, "values": landed,
                    "by": step, "heads": len(capable), "missing": missing,
                    "clipped": clipped, "relative": True,
                    "summary": (f"{resolved} {step:+g} on {len(capable)} of "
                                f"{len(heads)} head(s){note}")}

        if resolved in HTP_ROLES:
            applied = _clamp(int(round(float(value))), 0, 100)
            for h in capable:
                for r, v in self._level_values(h, applied).items():
                    self._set_programmer(h["head_no"], r, v)
        else:
            applied = _clamp(int(round(float(value))), 0, full)
            for h in capable:
                self._set_programmer(h["head_no"], resolved, applied)
        missing = [h["head_no"] for h in heads
                   if resolved not in (h.get("map") or [])]
        note = (f"; {len(missing)} head(s) have no {resolved} channel and were "
                f"left alone" if missing else "")
        clamped = applied != int(round(float(value)))
        if phys:
            # The operator typed a DEGREES number, so the message has to be
            # in degrees.  Echoing the pre-conversion logical value (the
            # internal 141992 that stands for 900 on a 540-degree pan) puts
            # a number in front of them that means nothing to them, and is
            # the one case where "asked for X" reads as nonsense.
            got = _deg(_logical_to_phys(applied, rng["min"], rng["max"], full))
            if clamped:
                note += (f" (asked for {_deg(asked):g}°, clamped to {got:g}°"
                         f" — this head travels {rng['min']:g}..{rng['max']:g}°)")
            else:
                phys_note = f" = {got:g}°"
        elif clamped:
            # Report what was STORED, not what was asked for.  Echoing the
            # request would put 9999 in the encoder while the light was
            # sent 255 - the encoder would then lie until the next
            # repaint, and the operator would trust it.
            note += f" (asked for {value:g}, clamped to {applied})"
        out = {"attribute": resolved, "value": applied,
               "requested": int(round(float(value))), "clamped": clamped,
               "full": full,
               "heads": len(capable), "missing": missing,
               "partial": bool(missing)}
        if phys:
            out["unit"] = "degree"
            out["min"], out["max"] = rng["min"], rng["max"]
            out["requested_phys"] = _deg(asked)
            out["phys"] = got
        out["summary"] = (f"{resolved} = {applied} on {len(capable)} of "
                          f"{len(heads)} head(s){note}{phys_note}")
        return out

    def remap_heads(self, heads: list[int] | None = None) -> dict:
        """Re-resolve patched heads against the CURRENT fixture library.

        A head's channel map is resolved from its profile, and the profile
        lives in a database the operator can edit.  So without this, fixing
        a channel's role leaves every already-patched head un-d drivable
        until a restart - which reads as the fix having done nothing at
        all, and is the reason a fixture editor bolted onto an existing
        library tends to be abandoned.

        Programmer values for roles that no longer exist are DROPPED, not
        left as orphans: a value for a channel the head does not have can
        never be merged into a frame, so keeping it would mean the channel
        sheet kept showing a "set" that does nothing.
        """
        with self.lock:
            _FIXTURE_CACHE.clear()
            fixtures.invalidate_cache()
            self._range_cache.clear()
            self._source_cache.clear()
            target = ({int(h) for h in heads} if heads
                      else {h["head_no"] for h in self.patch})
            changed, dropped, gained = [], [], []
            for head in self.patch:
                if head["head_no"] not in target:
                    continue
                before = list(head.get("map") or [])
                mapping, mode_name, mapped = self._resolve_map(
                    head.get("manufacturer"), head.get("model"),
                    head.get("mode"), head.get("channels"))
                if mapping == before:
                    continue
                # A role that vanished from the profile cannot be merged
                # into a frame, so its value is dead weight.
                lost = set(before) - set(mapping)
                row = self.programmer.get(head["head_no"])
                if row:
                    for role in lost:
                        if row.pop(role, None) is not None:
                            dropped.append({"head": head["head_no"],
                                            "role": role})
                    if not row:
                        self.programmer.pop(head["head_no"], None)
                for role in set(mapping) - set(before):
                    if role not in ("raw", "unused"):
                        gained.append({"head": head["head_no"], "role": role})
                head["map"] = mapping
                head["mode"] = mode_name
                head["mapped"] = mapped
                changed.append(head["head_no"])
            if changed:
                self.patch_rev += 1
            still_raw = sorted(h["head_no"] for h in self.patch
                               if "raw" in (h.get("map") or []))
            return {"heads": len(changed), "changed": changed,
                    "gained": gained, "dropped": dropped,
                    "still_raw": still_raw,
                    "summary": (f"re-mapped {len(changed)} head(s); "
                                + (f"{len(gained)} channel(s) gained a control"
                                   if gained else "no new controls")
                                + (f"; {len(dropped)} stale value(s) dropped"
                                   if dropped else "")
                                + (f"; {len(still_raw)} head(s) still have "
                                   f"uncontrollable channels"
                                   if still_raw else ""))}

    def _a_remap_heads(self, heads=None, **_):
        """Action wrapper, so the fix is reachable from the console API and
        the agent - not only from the route that happens to call it."""
        return self.remap_heads(heads)

    def _looks(self, now: float | None = None) -> list[dict]:
        """[{n, hex, a, on, pan?, tilt?}] per head - the display look.

        Built straight from _resolve_head, so it is exactly what
        build_frames will put on the wire (same HTP/LTP merge, same
        blackout and master) - there is no second, drifting model.

        `pan` / `tilt` are the head's ACTUAL aim, 0..1, and are present
        only when the fixture has that channel and something is driving
        it.  Without them the visualiser has to invent an aim from the
        landing point, so a moving head whose pan/tilt the operator moved
        would sit still while its beam pointed somewhere else - the head
        looks broken even though the DMX is correct.  Absent means "not
        driven", and the visualiser keeps its geometric default.
        """
        now = time.monotonic() if now is None else now
        prog = self.programmer
        pb_vals = self._active_playbacks(now)
        fx_vals = self._fx_values(now)
        out = []
        for head in self.patch:
            values = self._resolve_head(head, prog, pb_vals,
                                        fx_vals.get(head["head_no"]))
            intensity = None
            for role in HTP_ROLES:
                if role in values:
                    v = values[role]
                    intensity = v if intensity is None else max(intensity, v)
            if intensity is None:
                # Dimmer-less fixture (moving head in a short mode, a
                # 3-channel RGB par, ...): the lamp is either passing
                # light or it is not, so report the gate state, not 0.
                intensity = self._gate_intensity(head, values)
            pct = _curve_pct(intensity or 0, head.get("curve", "linear"))
            row = {"n": head["head_no"],
                   "a": round(max(0, min(100, pct)) / 100.0, 3),
                   "hex": self._hex_for(head, values),
                   "on": pct > 0}
            pan = self._aim01(head, values, "pan")
            tilt = self._aim01(head, values, "tilt")
            if pan is not None:
                row["pan"] = pan
            if tilt is not None:
                row["tilt"] = tilt
            # THE FIXTURE'S OWN TRAVEL, so the beam is drawn where the head
            # is actually pointing.  The visualiser used a hardcoded 270
            # degrees of tilt, which is a 540-degree-pan/270-degree-tilt
            # convention from the manual.  A Chauvet Intimidator's file
            # says -117..+117, so the same desk value drew the beam 12
            # degrees short at each end - and nothing on screen could tell
            # you, because the drawing looked perfectly plausible.
            #
            # Only sent when the profile actually declares a range, so a
            # generic with no GDTF keeps the historical behaviour and
            # gains no bytes.
            ranges = self.head_ranges(head)
            span = {}
            for role, key in (("pan", "pan"), ("tilt", "tilt")):
                r = ranges.get(key)
                if r and r.get("min") is not None and r.get("max") is not None \
                        and r.get("unit") == "degree":
                    span[role] = [r["min"], r["max"]]
            if span:
                row["deg"] = span
            out.append(row)
        return out

    @staticmethod
    def _aim01(head: dict, values: dict, base: str) -> float | None:
        """A pan/tilt channel as 0..1, or None when the head has no such
        channel or nothing is driving it.

        16-bit heads (pan + pan_fine) carry ONE logical value across two
        DMX bytes, so they are normalised against 65535 and the fine
        channel's explicit operator value wins - exactly what build_frames
        writes.  8-bit heads are normalised against 255.  Getting this
        wrong is what makes a head look like it is aiming at the ceiling
        when the desk says it is at centre.
        """
        if base not in values and base + "_fine" not in values:
            return None
        fine_role = base + "_fine"
        is16 = fine_role in (head.get("map") or [])
        if is16:
            coarse, fine = split_16bit(_logical16(values.get(base, 0)))
            if fine_role in values:
                fine = max(0, min(255, int(values[fine_role])))
            logical = (coarse << 8) | fine
            return round(logical / 65535.0, 4)
        v = values.get(base, 0)
        return round(max(0, min(255, int(v))) / 255.0, 4)

    def look_feed(self, since: int | None = None) -> dict:
        """Per-tick look feed for the visualiser (see §13 fades).

        The lite feed only carries heads when the patch revision changes,
        which is right for structure but wrong for light: a 4-second cue
        fade must animate, not step ten times.  This endpoint is the light
        feed - only heads that are actually emitting, so a 200-head rig
        costs a few hundred bytes per tick - and it carries a monotonic
        sequence number the client uses to interpolate between ticks.

        `since` is a hint only: an unknown or too-old sequence returns a
        full snapshot, which is always correct, never a gap.
        """
        with self.lock:
            looks = self._looks()
            self._look_seq += 1
            seq = self._look_seq
            lit = [row for row in looks if row["a"] > 0]
            return {"seq": seq, "hz": self.output.get("hz", config.DMX_HZ),
                    "full": since is None or since != seq - 1,
                    "heads": lit, "count": len(looks),
                    "lit": len(lit)}

    def _look(self, now: float | None = None) -> list[dict]:
        """Lite-feed look rows: {n, look:{hex, a, on}} (patch-revisioned)."""
        return [{"n": row["n"],
                 "look": {"hex": row["hex"], "a": row["a"],
                          "on": row["on"]}}
                for row in self._looks(now)]

    def _gate_intensity(self, head: dict, values: dict) -> int:
        """Brightness of a fixture that has no dimmer channel (0 or 100).

        Its shutter/strobe channel gates the lamp, so light passes when
        the resolved value is at or above the fixture's open value; with
        no colour channels to look at, "on" then means "open".  Mirrors
        build_frames exactly: an un-driven gate is written 0, so an idle
        head reads dark here too.
        """
        role = self._shutter_role(head)
        if role is None:
            # No dimmer and no gate (e.g. a raw 3ch RGB par): the colour
            # channels ARE the brightness.
            colour = [v for r, v in values.items()
                      if r in _COLOUR_ROLES]
            return round(max(colour) * 100 / 255) if colour else 0
        return 100 if values.get(role, 0) >= self._open_value(head, role) else 0

    @staticmethod
    def _hex_for(head: dict, values: dict) -> str:
        roles = set(head["map"])
        if roles & {"red", "green", "blue"}:
            r = values.get("red", 0)
            g = values.get("green", 0)
            b = values.get("blue", 0)
            if r or g or b or "red" in values:
                return f"#{r:02x}{g:02x}{b:02x}"
        elif roles & {"cyan", "magenta", "yellow"}:
            if any(k in values for k in ("cyan", "magenta", "yellow")):
                r = 255 - values.get("cyan", 255)
                g = 255 - values.get("magenta", 255)
                b = 255 - values.get("yellow", 255)
                return f"#{max(0, r):02x}{max(0, g):02x}{max(0, b):02x}"
        elif "white" in values:
            w = values["white"]
            return f"#{w:02x}{w:02x}{w:02x}"
        return ROLE_HEX.get(head.get("role") or "generic",
                            ROLE_HEX["generic"])

    # ------------------------------------------------------------------
    # show files
    # ------------------------------------------------------------------
    def _safe_name(self, name) -> str:
        text = str(name or "").strip()
        if not SAFE_NAME.match(text):
            raise ValueError("show names use letters, digits, space, - and _ "
                             "(max 40 characters)")
        return text

    def _show_names(self) -> list[str]:
        try:
            return sorted(p.stem for p in self.show_dir.glob("*.json")
                          if not p.name.startswith("."))   # no dotfiles
        except OSError:
            return []

    # -- autosave (progress survives a crash; save_show stays a user act) --
    AUTOSAVE_MIN_INTERVAL = 1.0          # seconds between throttled writes

    def _autosave_payload(self) -> str:
        """Serialise the whole console state. Caller holds the lock.

        Pure serialisation, no I/O, so the background writer can own the
        disk without ever blocking the output thread.
        """
        payload = {
            "version": 1,
            "saved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "patch": self.patch,
            "groups": self.groups,
            "palettes": self.palettes,
            "presets": self.presets,
            "playbacks": [self._pb_saved(pb) for pb in self.playbacks],
            "programmer": {str(k): dict(v)
                           for k, v in self.programmer.items()},
            "selected": list(self.selected),
            "venue": self.venue,
            "meta": {"master": self.master,
                     "show_file": self.show_file},
        }
        return json.dumps(payload, indent=2)

    def _autosave(self, force: bool = False) -> bool:
        """Queue an autosave; a background writer owns the disk.

        Measured: serialising + writing a 120-head rig costs ~3 ms, which
        is 12% of a 40 Hz frame budget.  Doing that under the engine lock
        meant a visible hitch once a second, in the middle of a fade.  Now
        the serialisation happens under the lock (a few hundred us, and
        the data must be a consistent snapshot) and the write happens on
        the writer thread.
        """
        if self.autosave_path is None:
            return False
        now = time.monotonic()
        if not force and (now - self._autosave_at) < self.AUTOSAVE_MIN_INTERVAL:
            return False
        with self.lock:
            try:
                text = self._autosave_payload()
            except (TypeError, ValueError):
                return False
            path = self.autosave_path
        if not self._autosave_queue(text, path, force=force):
            return False
        self._autosave_at = now
        return True

    def _autosave_queue(self, text: str, path: Path,
                        force: bool = False) -> bool:
        """Hand the serialised state to the writer (or write it inline)."""
        if self._writer is None or not self._writer.is_alive():
            # No writer yet (tests, or shutdown): write inline so the file
            # still exists.  This is the one path that blocks, and it is
            # never on the output thread.
            return self._write_autosave(text, path)
        self._writer_pending = text
        self._writer_event.set()
        if force:
            # The caller wants it on disk NOW (shutdown): wait for the
            # writer to catch up rather than returning a lie.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                with self._writer_lock:
                    if self._writer_pending is None:
                        return True
                time.sleep(0.005)
            return self._write_autosave(text, path)
        return True

    @staticmethod
    def _write_autosave(text: str, path: Path) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError:
            return False

    def _autosave_writer(self) -> None:
        """Background writer: one deep queue, newest state wins."""
        while not self._writer_stop.is_set():
            self._writer_event.wait(0.25)
            self._writer_event.clear()
            while not self._writer_stop.is_set():
                with self._writer_lock:
                    text, path = self._writer_pending, self._writer_path
                    self._writer_pending = None
                if text is None or path is None:
                    break
                self._write_autosave(text, path)

    def _start_writer(self) -> None:
        if self._writer is not None or self.autosave_path is None:
            return
        self._writer_path = self.autosave_path
        self._writer_stop.clear()
        self._writer = threading.Thread(target=self._autosave_writer,
                                       name="jarvis-autosave", daemon=True)
        self._writer.start()

    def _stop_writer(self) -> None:
        writer = self._writer
        if writer is None:
            return
        self._writer_stop.set()
        self._writer_event.set()
        writer.join(timeout=3.0)
        self._writer = None
        # Anything still queued gets one final synchronous write, so a
        # shutdown never loses the last change.
        with self._writer_lock:
            text, path = self._writer_pending, self._writer_path
            self._writer_pending = None
        if text is not None and path is not None:
            self._write_autosave(text, path)

    def _restore_autosave(self) -> bool:
        """Load the autosaved progress on boot.  Silent no-op when absent."""
        if self.autosave_path is None or not self.autosave_path.is_file():
            return False
        try:
            payload = json.loads(self.autosave_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        try:
            heads = [self._head_from_layout(h)
                     for h in payload.get("patch") or []]
            self._replace_patch(heads)
            self.groups = list(payload.get("groups") or [])
            saved_palettes = self._normalize_palettes(payload.get("palettes"))
            for key in self.palettes:
                self.palettes[key] = list(saved_palettes.get(key) or [])
            self.presets = [dict(q) for q in (payload.get("presets") or [])
                            if isinstance(q, dict)]
            self.playbacks = _normalize_playbacks(
                payload.get("playbacks") or [])
            patched = {h["head_no"] for h in self.patch}
            self.programmer = {}
            for k, row in (payload.get("programmer") or {}).items():
                try:
                    head_no = int(k)
                except (TypeError, ValueError):
                    continue
                if head_no in patched and isinstance(row, dict):
                    # 0-65535: 16-bit parameters (pan/tilt fine pairs)
                    # must survive a restart unchanged.
                    self.programmer[head_no] = {
                        str(role): _clamp(v, 0, 65535)
                        for role, v in row.items()}
            self.selected = [n for n in (int(x) for x in
                                         payload.get("selected") or [])
                             if n in patched]
            meta = payload.get("meta") or {}
            self.master = _clamp(meta.get("master", 100), 0, 100)
            self.show_file = meta.get("show_file") or self.show_file
            venue = payload.get("venue")
            if isinstance(venue, dict):
                try:
                    self.act("set_venue", venue=venue)
                except (ValueError, TypeError):
                    self.venue = {}       # a bad venue must not block boot
        except (ValueError, TypeError, KeyError):
            return False
        return True

    def _a_save_show(self, name="", **_):
        label = self._safe_name(name or "show")
        # Snapshot + serialise under the lock, write the file OUTSIDE it:
        # json.dumps of a 120-head rig is ~3 ms, which is 12% of a 40 Hz
        # frame budget, and SAVE SHOW is a user action that can land in
        # the middle of a fade.
        with self.lock:
            payload = {
                "version": 1,
                "saved": datetime.now(timezone.utc)
                .isoformat(timespec="seconds"),
                "patch": json.loads(json.dumps(self.patch, default=str)),
                "groups": json.loads(json.dumps(self.groups, default=str)),
                "palettes": json.loads(json.dumps(self.palettes,
                                                   default=str)),
                "presets": json.loads(json.dumps(self.presets, default=str)),
                "playbacks": [self._pb_saved(pb) for pb in self.playbacks],
                "meta": {"master": self.master},
            }
            text = json.dumps(payload, indent=2)
            self.show_file = label
        self.show_dir.mkdir(parents=True, exist_ok=True)
        path = self.show_dir / f"{label}.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return {"file": label, "show_file": label,
                "summary": f"saved show {label!r}"}

    @staticmethod
    def _normalize_rows(values) -> dict[int, dict]:
        """Re-key a {head_no: {role: value}} map after a JSON round-trip.

        json.dumps turns every int head key into a string ("1" instead of
        1) while merge-time lookups (_pb_values, _resolve_head,
        _a_include_palette) index the dict with the INT head number - so
        anything restored from disk silently resolves to nothing.  Returns
        int keys with DMX values clamped to 0-65535 (16-bit pairs ride in
        the same rows).
        """
        out: dict[int, dict] = {}
        for key, row in (values or {}).items():
            try:
                head_no = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            clean = {}
            for role, value in row.items():
                try:
                    clean[str(role)] = _clamp(int(value), 0, 65535)
                except (TypeError, ValueError):
                    continue
            out[head_no] = clean
        return out

    @classmethod
    def _normalize_stack(cls, stack) -> list[dict]:
        """Repair cue stacks that survived a JSON round-trip.

        See _normalize_rows: the head keys must be ints again or the whole
        playback stack would drive nothing after a restart - the classic
        "playback does not work" report.
        """
        out: list[dict] = []
        for cue in stack or []:
            if not isinstance(cue, dict):
                continue
            entry = dict(cue)
            entry["n"] = int(cue.get("n") or len(out) + 1)
            entry["name"] = str(cue.get("name") or f"Cue {entry['n']}")
            entry["values"] = cls._normalize_rows(cue.get("values"))
            entry["fade_s"] = float(cue.get("fade_s") or 0.0)
            entry["hold_s"] = float(cue.get("hold_s") or 0.0)
            out.append(entry)
        return out

    @classmethod
    def _normalize_palettes(cls, palettes) -> dict:
        out = {}
        for key, entries in (palettes or {}).items():
            fixed = []
            for entry in entries or []:
                if not isinstance(entry, dict):
                    continue
                row = dict(entry)
                row["values"] = _palette_values(entry.get("values"))
                fixed.append(row)
            out[str(key)] = fixed
        return out

    @staticmethod
    def _pb_saved(pb: dict) -> dict:
        f = pb["follow"]
        return {"n": pb["n"], "name": pb["name"], "stack": pb["stack"],
                "index": pb["index"], "active": pb["active"],
                "level": pb["level"],
                # The crossfade TIME, not the running fade.  Saving the
                # running one would reload a show mid-fade with a stale
                # start value, and saving nothing means the console forgets
                # it and the operator sets it again wondering why it was
                # there before.
                "xfade_s": (float(pb["xfade"]["dur"])
                            if pb.get("xfade") else None),
                "follow": {"on": f["on"], "delay": f["delay"],
                           "paused": f["paused"], "loop": f["loop"]}}

    @staticmethod
    def _stale_heads(heads: list[dict], playbacks: list[dict]) -> list[int]:
        """Heads a saved stack still references that are no longer patched.

        The rows stay in the cue (deleting them would be data loss, and they
        are inert at merge time - an unpatched head is simply never looked
        up), but the operator should be told, because a stack that is 100%
        stale is exactly the "playback does nothing" report.
        """
        patched = {h["head_no"] for h in heads}
        stale: set[int] = set()
        for pb in playbacks:
            for cue in pb.get("stack") or []:
                stale.update(int(n) for n in (cue.get("values") or {})
                             if int(n) not in patched)
        return sorted(stale)

    @staticmethod
    def _apply_follow(base: dict, saved: dict) -> None:
        """Restore follow config from a saved playback row.

        The deadline (`at`) is monotonic - process-local - so it always
        restarts as None; a restored follow re-arms on the next cue step
        or follow_set instead of firing against a stale timestamp.
        """
        row = (saved or {}).get("follow") or {}
        f = base["follow"]
        try:
            f["delay"] = max(0.0, float(row.get("delay") or 0.0))
        except (TypeError, ValueError):
            f["delay"] = 0.0
        f["on"] = bool(row.get("on"))
        f["paused"] = bool(row.get("paused"))
        f["loop"] = bool(row.get("loop"))
        f["at"] = None

    def _a_load_show(self, name="", **_):
        label = self._safe_name(name)
        path = self.show_dir / f"{label}.json"
        if not path.is_file():
            raise ValueError(f"no show file {label!r}")
        # Slow read phase, unlocked: the file read and the fixture-mode
        # resolution both touch the disk.  Nothing below mutates state
        # until the commit block.
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read show {label!r}: {exc}") from exc
        heads = [self._head_from_layout(h) for h in payload.get("patch") or []]
        groups = list(payload.get("groups") or [])
        palettes = self._normalize_palettes(payload.get("palettes"))
        presets = [dict(q) for q in (payload.get("presets") or [])
                   if isinstance(q, dict)]
        playbacks = _normalize_playbacks(payload.get("playbacks") or [])
        master = _clamp((payload.get("meta") or {}).get("master", 100), 0, 100)

        # Commit phase: swap the whole show in under the lock.  A bad patch
        # raises from _replace_patch, which rolls the patch back, and the
        # remaining state is untouched because we have not assigned it yet.
        with self.lock:
            self._replace_patch(heads)
            self.groups = groups
            for key in self.palettes:
                self.palettes[key] = list(palettes.get(key) or [])
            self.presets = presets
            self.playbacks = playbacks
            self.master = master
            self.show_file = label
        return {"file": label, "heads": len(heads), "show_file": label,
                "stale_heads": self._stale_heads(heads, playbacks),
                "summary": f"loaded show {label!r} ({len(heads)} heads)"}

    def _a_import_show(self, concept=None, playback=None, name="", **_):
        """Turn a show-design concept into a playable cue stack."""
        if not isinstance(concept, dict):
            raise ValueError("concept object is required")
        data = concept
        if not data.get("cues") and data.get("concepts"):
            data = (data.get("concepts") or [{}])[0]
        cues = data.get("cues") or []
        if not cues:
            raise ValueError("concept has no cues")
        if not self.patch:
            raise ValueError("patch is empty - load a layout first")
        pb = self._playback(playback if playback is not None else 1)
        colours = data.get("colours") or {}
        intensity = data.get("intensity") or {}
        active = data.get("active")
        if isinstance(active, dict):
            active_roles = {k for k, v in active.items() if v}
        elif isinstance(active, list):
            active_roles = {str(r) for r in active}
        else:
            active_roles = set()

        # A concept names DESIGN roles (wash/beam/spot/...), but a patch
        # imported from CSV or hand-written can be all "generic".  When
        # the two vocabularies do not intersect, applying the concept
        # literally lights NOTHING - every cue would come out as
        # {"dimmer": 0} and playback would look broken.  So fall back to
        # "every head participates" (and take the level/colour from
        # whichever role the concept did specify).
        patch_roles = {str(h.get("role") or "generic") for h in self.patch}
        # role -> the head numbers it would drive, so the UI can offer
        # "assign heads 2-5 to wash" instead of leaving the operator to
        # guess why the spots came up the wrong colour.
        role_heads: dict[str, list[int]] = {}
        for head in self.patch:
            role_heads.setdefault(str(head.get("role") or "generic"),
                                  []).append(head["head_no"])
        role_mismatch = False
        used_roles: set[str] = set()

        def level_for(table: dict, role: str) -> int:
            """Concept level for a role: role -> generic -> any declared."""
            if role in table:
                return _clamp(table[role], 0, 100)
            if "generic" in table:
                return _clamp(table["generic"], 0, 100)
            if table:
                for value in table.values():
                    return _clamp(value, 0, 100)
            return 0

        def colour_for(table: dict, role: str) -> str:
            return (table.get(role) or table.get("generic")
                    or next(iter(table.values()), "#ffffff"))

        stack = []
        for cue in cues:
            cue_active = cue.get("active")
            if isinstance(cue_active, dict):
                roles_on = {k for k, v in cue_active.items() if v}
            elif isinstance(cue_active, list):
                roles_on = {str(r) for r in cue_active}
            else:
                roles_on = active_roles or None
            if roles_on and not (roles_on & patch_roles):
                # Concept roles this patch has no heads for.  Light
                # everything (a black show is the worse failure) and
                # record what was asked for, so the result can name the
                # assumption and the UI can offer to assign roles.
                role_mismatch = True
                used_roles |= set(roles_on)
                roles_on = None
            cue_colours = cue.get("colours") or colours
            cue_intensity = cue.get("intensity") or intensity
            values = {}
            for head in self.patch:
                role = str(head.get("role") or "generic")
                on = roles_on is None or role in roles_on
                pct = level_for(cue_intensity, role) if on else 0
                hexcol = colour_for(cue_colours, role)
                row = {}
                if pct > 0:
                    try:
                        row.update(self._colour_values(head, hexcol))
                    except ValueError:
                        row.update(self._white_values(head))
                row.update(self._level_values(head, pct))
                values[head["head_no"]] = row
            stack.append({
                "n": len(stack) + 1,
                "name": str(cue.get("name") or f"Cue {len(stack) + 1}"),
                "fade_s": float(cue.get("fade_s") or 0.0),
                "hold_s": float(cue.get("hold_s") or 0.0),
                "values": values,
            })
        pb["stack"] = stack
        pb["index"] = -1
        pb["active"] = False
        pb["fade"] = None
        if name:
            pb["name"] = str(name)
        elif data.get("name"):
            pb["name"] = str(data["name"])
        self.patch_rev += 1                # notify lite clients
        note = " (patch roles unknown to the concept - all heads lit)" \
            if role_mismatch else ""
        result = {"playback": pb["n"], "cues": len(stack),
                  "name": pb["name"], "role_fallback": role_mismatch,
                  "summary": f"imported {len(stack)} cues into "
                             f"PB{pb['n']}{note}"}
        if role_mismatch:
            # Name the assumption and the fix: the concept wanted these
            # roles, the patch only has these.  The UI can offer to assign
            # the head ranges and re-import.
            result["concept_roles"] = sorted(used_roles)
            result["patch_roles"] = sorted(patch_roles)
            result["role_heads"] = {role: sorted(nums) for role, nums
                                    in sorted(role_heads.items())}
        return result

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

    def _output_public(self) -> dict:
        pub = dict(self.output)
        pub["dry_run"] = self.dry_run
        pub["transport"] = getattr(self._sender, "transport",
                                   config.DMX_TRANSPORT)
        if self._sender is not None:
            pub["host"] = self._sender.host + ":" + str(self._sender.port)
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
                out.append(pub)
                continue
            pub = {"id": row["id"], "role": row["role"], "kind": row["kind"],
                   "speed": row["speed"], "spread": row["spread"],
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
                "stack": [{"n": c["n"], "name": c["name"],
                           "fade_s": c["fade_s"], "hold_s": c["hold_s"],
                           "follow_s": c.get("follow_s"),
                           "empty": not (c.get("values") or {})}
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
                "blackout": self.blackout,
                "selected": list(self.selected),
                "programmer": {
                    "values": {str(k): dict(v)
                               for k, v in self.programmer.items()},
                    "attrs": self._touched_attrs(),
                },
                "patch": [dict(h) for h in self.patch],
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
                "blackout": self.blackout,
                # The lock rides in the hot feed so the client can grey out
                # what it refuses, rather than letting the operator find out
                # by pressing something during a show.
                "lock": self.lock_state,
                "lock_has_password": bool(getattr(self, "_lock_hash", "")),
                "selected": list(self.selected),
                "venue": self.venue,
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
