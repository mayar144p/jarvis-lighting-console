"""Module-level parts of the engine: constants, the action list, helpers.

Split out of app/engine.py so the Engine mixins (app/engine_*.py) can
share them without importing engine.py.  engine.py re-exports all of it,
so `from app.engine import X` keeps working.
"""
from __future__ import annotations

import csv as csvmod
import hashlib
import io
import collections
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app import config, fixtures, fixture_kind, motion as motion_mod
from app import fx as fxmod
from app import fxlib as fxlib_mod
from app import profiles
from app import timeline as tl_mod
from app import venue as venue_mod
from app import merge
from app import netif
from app.artnet import ArtNetSender
from app.sacn import SacnSender

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
    "patch_from_csv", "save_show", "load_show", "restore_version",
    "show_versions", "show_export",
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
    "status", "undo", "redo", "cue_go", "cue_back", "cue_forward", "quick_fader",
    # quick buttons are played, not edited: a flash is not an undo step
    "quick_press", "quick_release_all", "quick_rate", "group_flash",
    "ready_check", "show_versions", "show_export", "rdm_compare", "venue_preview", "step_capture", "rig_pieces", "rig_report", "paperwork", "colour_cal_get", "pad_info", "venue_info",
    # the timeline's transport is playing the show, not editing it
    "timeline_play", "timeline_pause", "timeline_stop", "timeline_seek",
    "blackout", "master", "playback_level", "playback_activate",
    "playback_release", "set_output", "follow_set", "locate",
    # calibrating a fixture model's speed is library setup, and the test
    # moves restore themselves
    "motion_set", "motion_test", "motion_test_end", "motion_get",
    # special effects are performed, not edited: never an undo step
    "fx_arm", "fx_fire", "fx_fog", "fx_laser", "fx_kill", "fx_reload",
    "fx_status", "remember_open", "light_test", "light_tested", "colour_cal", "teach_slots",
    # the Speed master is performed live, like the grand master; so is the tempo
    "speed_master", "floor_safe",
    "tempo_tap", "tempo_set", "tempo_sync", "tempo_nudge", "tempo_prodj", "tempo_link", "fx_beats", "fx_space", "fx_tweak",
    "step_fx_run", "highlight", "group_master",
    # a macro manages its own undo: its lines are ONE step
    "macro_run", "osc", "timecode", "blind",
    "sound_tempo", "autopilot", "autopilot_next",
    # where the DMX goes is desk setup, not an edit to the show
    "set_dmx_target", "virtual_node",
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
    # Read-only queries change nothing, so they must not cost an undo step
    # (the undo button used to read "undo: fx available").
    "fx_available", "get_limits", "cue_info", "export_patch",
    # Choosing WHICH lights to work on is not an edit to the show; on a
    # desk, undo walks back what you did to them, not what you clicked.
    "select_all", "select_group", "select_heads", "select_similar",
    "select_query", "clear_selection", "select_split",
})
# Ready? counts a DMX send error as a problem this long after it happened
READY_ERROR_WINDOW_S = 60.0

# Queries: they change nothing, so they do not make clients reload.
_READ_ONLY = frozenset({"status", "fx_available", "get_limits", "cue_info", "pad_info",
                        "export_patch", "venue_info", "motion_get",
                        "fx_status", "ready_check", "show_versions", "show_export", "rdm_compare",
                        "venue_preview", "step_capture", "rig_pieces", "rig_report", "paperwork", "colour_cal_get", "paperwork"})

# Actions where a run of calls is one intent, so they collapse into a
# single step.  Only genuinely CONTINUOUS ones belong here: a value the
# operator is dragging or typing into.  Discrete edits must not coalesce -
# recording two cues 400 ms apart is two cues, and collapsing them would
# throw the first away.
UNDO_COALESCE = frozenset({
    "set_intensity", "set_attribute", "set_colour", "set_position",
    "set_address", "set_place", "aim_at", "nudge", "quick_xy",
})

# --- channel roles ------------------------------------------------------
# The vocabulary, the 16-bit maths and the dimmer curves live in
# engine_support so the hot path (app/merge.py) can use them without
# importing this module - a merge must never drag in the fixture
# database, the HTTP layer or the output thread.  Re-exported here
# because `from app.engine import HTP_ROLES` is the established entry
# point for the tests and the profile tooling.
from . import engine_support as _engine_support
from .merge import FX_OUTPUT_ROLES
from .engine_support import (ATTRIBUTE_ALIAS as _ATTRIBUTE_ALIAS,  # noqa: F401
                             LASER_ROLES, FX_ROLES,
                             BEAM_ROLES, COLOUR_ROLES, HTP_ROLES,
                             ROLE_HEX, ROLES, SLOTS,
                             channel_role, curve_pct as _curve_pct,
                             is_fine_role, join_16bit,  # noqa: F401 - re-exported
                             logical16 as _logical16,
                             pos as _pos,
                             pos_to_ua as _pos_to_ua, split_16bit)


# Channels the light feed reports so the 3D beam can be shaped by them.
_BEAM_LOOK_ROLES = ("zoom", "iris", "frost", "focus", "gobo", "gobo_rot",
                    "prism", "strobe", "shutter")

# Colour roles: on a fixture with no dimmer these ARE the brightness.
_COLOUR_ROLES = COLOUR_ROLES
VDIM = merge.VDIM            # a dimmer-less light's virtual intensity (0-100)

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


def default_mode(modes: list[dict]) -> dict:
    """The mode to patch when none is asked for: the fewest channels that
    still give real control - a dimmer, shutter or colour, and pan + tilt
    when the light has them at all.  Fewest-first alone picked 1-channel
    "sound active" modes that the desk can't control."""
    def roles(m):
        return {channel_role(c) for c in m.get("channels") or []}
    if not modes:
        return {}
    moves = any({"pan", "tilt"} <= roles(m) for m in modes)
    out = {"fx_fire", "fog"}
    fires = any(roles(m) & out for m in modes)
    if fires:
        # an SFX machine: a mode that can actually fire (a Psyco2Jet's
        # first "safety" mode has only its arm channel; a fogger's timer
        # mode has only its interval and duration)
        usable = [m for m in modes if roles(m) & out]
        return min(usable, key=lambda m: (m.get("channel_count") or len(m.get("channels") or []), modes.index(m)))
    # a laser: the mode that controls it most (pattern, colour, size,
    # position...) - its 1-channel "auto / sound" mode can't be programmed,
    # and the 3D can't draw a laser it can't read
    laser = lambda m: {r for r in roles(m) if r.startswith("laser_")}  # noqa: E731
    most = max((len(laser(m)) for m in modes), default=0)
    if most:
        usable = [m for m in modes if len(laser(m)) == most]
        return min(usable, key=lambda m: (m.get("channel_count") or len(m.get("channels") or []), modes.index(m)))
    light = {"dimmer", "shutter", "strobe", "red", "white", "wheel"}
    width = lambda m: m.get("channel_count") or len(m.get("channels") or [])  # noqa: E731
    wide = any(width(m) >= 2 for m in modes)
    usable = [m for m in modes if roles(m) & light and (not moves or {"pan", "tilt"} <= roles(m))
              and (width(m) >= 2 or not wide)]       # a 1-channel "shows" mode is a program picker
    pool = usable or modes
    # full control first: colour mixing when any mode mixes (an RGB bar's
    # "colour macro" modes only pick presets - the picker can't reach most
    # colours), then the fewest channels (no dimmer is fine: a light with
    # colour emitters gets a virtual one)
    mix = {"red", "green", "blue"}
    mixes = any(mix <= roles(m) for m in modes)
    emit = max((len(roles(m) & {"white", "warm_white", "cool_white", "amber", "uv", "lime"}) for m in modes), default=0)
    return min(pool, key=lambda m: (
        bool(mixes and not mix <= roles(m)),
        bool(not mixes and emit and len(roles(m) & {"white", "warm_white", "cool_white", "amber", "uv", "lime"}) < emit),
        width(m), modes.index(m)))


def _fclamp(value, low: float, high: float) -> float:
    """_clamp for numbers that aren't whole (speeds, sizes)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"not a number: {value!r}") from None
    if v != v:
        raise ValueError("not a number")
    return low if v < low else (high if v > high else v)


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


def _hex_or_none(value) -> str | None:
    """A #rrggbb colour as typed, or None for none."""
    text = str(value or "").strip()[:9]
    return text or None


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
    from app.engine_shows import ShowMixin   # a mixin that imports this module
    playbacks = [_new_playback(i + 1) for i in range(PLAYBACK_COUNT)]
    for pb in saved or []:
        try:
            num = int(pb.get("n") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if not 1 <= num <= len(playbacks):
            continue
        base = playbacks[num - 1]
        stack = ShowMixin._normalize_stack(pb.get("stack"))
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
                                 if pb.get("xfade_s") is not None else None),
                     "tracking": bool(pb.get("tracking")), "mib": bool(pb.get("mib"))})
        ShowMixin._apply_follow(base, pb)
    return playbacks


def _new_playback(n: int) -> dict:
    # follow = auto-advance of the cue stack: `at` is a monotonic
    # deadline (process-local, never saved); delay 0 means "use each
    # cue's hold time" (see _arm_follow).
    return {"n": n, "name": "", "stack": [], "index": -1, "active": False,
            "level": 100, "order": 0, "fade": None,
            "tracking": False, "mib": False,
            "follow": {"on": False, "delay": 0.0, "paused": False,
                       "loop": False, "at": None}}


# Action allowlist.  Every name needs an _a_<name> method on Engine.
ACTIONS = (
    "add_heads", "auto_patch", "blackout", "clear_heads",
    "clear_programmer", "clear_attrs",
    "clear_selection", "cue_back", "cue_forward", "cue_go", "redo", "undo",
    "record_preset", "include_preset", "delete_preset", "rename_preset",
    "insert_cue", "delete_cue", "move_cue", "rename_cue", "edit_cue",
    "cue_info", "set_attr_range", "remap_heads",
    "follow_set", "group_create", "group_delete", "import_scan",
    "import_show", "include_palette", "locate", "load_show", "master",
    "patch_clear", "patch_from_csv", "patch_list",
    "playback_activate", "playback_level", "playback_release",
    "record_cue", "record_palette", "remove_heads", "run_command",
    "run_fx", "save_show", "fx_available",
    "select_all", "select_group", "select_heads", "select_similar", "select_split",
    "select_query", "set_address", "rename_head", "fan",
    "align", "distribute", "mirror", "export_patch",
    "set_limits", "clear_limits", "set_orient", "get_limits",
    "set_lock", "unlock", "set_dry_run",
    "set_attribute", "set_colour", "set_intensity", "set_output",
    "set_dmx_target", "virtual_node", "set_place", "set_position", "set_venue", "status", "stop_fx",
    "venue_template", "venue_room", "venue_stage", "venue_add",
    "venue_shape", "venue_build", "venue_describe", "venue_array", "venue_align", "venue_ceiling", "venue_preview", "quick_from_programmer",
    "venue_update", "venue_remove", "venue_underlay", "venue_crowd",
    "venue_camera", "venue_info", "attach_heads", "place_many",
    "quick_set", "quick_press", "quick_release_all", "quick_defaults",
    "quick_page", "quick_move", "quick_rate", "group_flash", "quick_from_laser", "quick_style", "quick_layout",
    "venue_save", "venue_open", "venue_delete",
    "patch_move_free", "change_type", "venue_rig", "ready_check", "show_versions", "restore_version", "rdm_compare",
    "show_export",
    "move_save", "move_play", "move_delete", "move_rename",
    "aim_at", "timeline_set", "timeline_track", "timeline_clip",
    "timeline_from_playback", "timeline_play", "timeline_pause",
    "timeline_stop", "timeline_seek",
    "motion_set", "motion_test", "motion_test_end", "motion_get",
    "fx_arm", "fx_fire", "fx_fog", "fx_laser", "fx_kill", "fx_reload",
    "fx_status", "quick_fx_defaults", "remember_open", "light_test", "light_tested",
    "speed_master", "aim_spot", "nudge", "move_range", "floor_safe",
    "tempo_tap", "tempo_set", "tempo_sync", "tempo_nudge", "tempo_prodj", "tempo_link", "fx_beats", "fx_tweak",
    "sound_link", "sound_trigger", "sound_tempo", "autopilot", "autopilot_next", "fx_space",
    "step_capture", "step_fx_save", "step_fx_delete", "step_fx_run", "chase_colours", "timeline_build", "quick_quant",
    "run_gradient", "run_media", "media_save", "media_delete",
    "shape_save", "shape_delete", "run_shape", "pad_info",
    "highlight", "park", "unpark", "group_master",
    "macro_save", "macro_delete", "macro_run", "osc", "timecode",
    "playback_mode", "cue_set", "blind",
    "rig_pieces", "rig_add", "rig_trim", "rig_report", "paperwork", "colour_cal", "colour_cal_get", "teach_slots", "quick_fader", "quick_xy", "roam",
)


# a slot name that means the laser is off (not a mode it runs in)
_OFFISH = re.compile(r"\b(off|blackout|disabled?|stop)\b", re.I)

DMX_TARGET_DEFAULT = {"mode": "auto", "host": "", "transport": ""}
DMX_TARGET_MODES = ("auto", "node", "broadcast", "usb")


def clean_dmx_target(raw) -> dict:
    """A stored output target -> {mode, host, transport}; junk -> auto."""
    import ipaddress
    raw = raw if isinstance(raw, dict) else {}
    mode = str(raw.get("mode") or "auto").lower()
    mode = mode if mode in DMX_TARGET_MODES else "auto"
    transport = str(raw.get("transport") or "").lower()
    transport = transport if transport in ("artnet", "sacn") else ""
    host = str(raw.get("host") or "").strip()
    if mode == "usb":                  # a USB DMX box: host is its serial port
        from app import usbdmx
        if usbdmx.valid_port(host):
            return {"mode": "usb", "host": host, "transport": "usbpro"}
        mode, host = "auto", ""
    if host and host != "multicast":
        try:
            host = str(ipaddress.IPv4Address(host))
        except ValueError:
            host = ""
    if mode == "node" and (not host or host == "multicast"):
        mode = "auto"
    return {"mode": mode, "host": host if mode != "auto" else "",
            "transport": transport}


def pick_auto_broadcast(ifaces: list[dict]) -> str:
    """The broadcast of the adapter most likely to face the rig: the
    Art-Net 2.x range first, then 10.x, then any private network."""
    def rank(i):
        ip = i["ip"]
        if ip.startswith("2."):
            return 0
        if ip.startswith("10."):
            return 1
        if ip.startswith(("192.168.", "172.")):
            return 2
        return 3
    if not ifaces:
        return "255.255.255.255"
    best = min(ifaces, key=rank)
    return netif.broadcast_for(best["ip"], best.get("mask"))


# A "not given" marker for action parameters that need to tell three states
# apart.  `follow` is one: a number, an explicit zero (wait), or absent
# (inherit the stack default).  None is already spoken for, so the default
# cannot be.
_UNSET = object()

__all__ = [
    'ACTIONS',
    'ArtNetSender',
    'BEAM_ROLES',
    'COLOUR_ROLES',
    'DMX_TARGET_DEFAULT',
    'DMX_TARGET_MODES',
    'FALLBACK_FOOTPRINTS',
    'FX_OUTPUT_ROLES',
    'FX_ROLES',
    'HISTORY_LIMIT',
    'HTP_ROLES',
    'LASER_ROLES',
    'MAX_UNIVERSES',
    'PALETTE_KINDS',
    'PLAYBACK_COUNT',
    'PRESET_ROLES',
    'Path',
    'READY_ERROR_WINDOW_S',
    'ROLES',
    'ROLE_HEX',
    'SAFE_NAME',
    'SELF_LOCKED_ACTIONS',
    'SLOTS',
    'SacnSender',
    'UNDO_COALESCE',
    'UNDO_COALESCE_S',
    'UNDO_EXCLUDED',
    'UNDO_LIMIT',
    'VDIM',
    '_ATTRIBUTE_ALIAS',
    '_BEAM_LOOK_ROLES',
    '_COLOUR_ROLES',
    '_FIXTURE_CACHE',
    '_GENERIC_MOVING',
    '_GENERIC_PACK',
    '_GENERIC_PAR',
    '_GENERIC_RGB',
    '_GENERIC_RGBW',
    '_GENERIC_STROBE',
    '_LABEL_ROLE',
    '_LEVELS_CACHE',
    '_OFFISH',
    '_READ_ONLY',
    '_UNSET',
    '_ZONE_RE',
    '_attr_role',
    '_clamp',
    '_copy_playbacks',
    '_curve_pct',
    '_deg',
    '_engine_support',
    '_fallback_fixture',
    '_hex_or_none',
    '_is_int',
    '_is_num',
    '_logical16',
    '_logical_to_phys',
    '_new_playback',
    '_normalize_playbacks',
    '_num',
    '_palette_values',
    '_parse_hex',
    '_phys_to_logical',
    '_pos',
    '_pos_to_ua',
    '_round2',
    '_secrets_equal',
    '_similar',
    '_truthy',
    'attr_domain',
    'channel_role',
    'clean_dmx_target',
    'collections',
    'config',
    'csvmod',
    'datetime',
    'default_mode',
    'fixture_kind',
    'fixtures',
    'fxlib_mod',
    'fxmod',
    'hashlib',
    'io',
    'is_fine_role',
    'join_16bit',
    'json',
    'math',
    'merge',
    'motion_mod',
    'netif',
    'os',
    'pick_auto_broadcast',
    'profiles',
    're',
    'split_16bit',
    'threading',
    'time',
    'timezone',
    'tl_mod',
    'venue_mod',
]
