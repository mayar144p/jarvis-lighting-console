"""The DMX merge: resolve per-role values, then write 512-byte frames.

This is the one piece of the console that runs on a hard deadline.  The
output thread calls build_frames() every 25 ms at 40 Hz while HTTP
threads mutate the patch, so:

  * no I/O in here - no file, no database, no socket.  Everything the
    merge needs is passed in.
  * no state of its own - no instance, no module globals that change.
    The patch, the programmer and the playback levels are arguments.
  * the wire and the visualiser read the SAME resolved values (see
    Engine._looks, which calls resolve_head too), so what you see is
    what goes out.

Keeping it here rather than inside Engine also means the hot path can be
benchmarked and unit-tested on its own, without constructing a console.
"""
from __future__ import annotations

from .engine_support import (COLOUR_ROLES, HTP_ROLES, SLOTS, curve_pct as _curve_pct,
                             is_fine_role, logical16 as _logical16,
                             split_16bit)

__all__ = ["resolve_head", "htp_value", "build_frames", "pair_map"]


def htp_value(values: dict, role: str):
    """The HTP value a row carries for `role`, or None if it has none.

    A zone-dimmer row answers for a plain dimmer and vice versa, which is
    what lets a 3-zone RGBW bar follow the same intensity control.
    """
    if role in values:
        return values[role]
    if "dimmer" in values:
        return values["dimmer"]
    if "zone_dimmer" in values:
        return values["zone_dimmer"]
    return None


def resolve_head(head: dict, prog: dict, pb_vals: list[tuple[int, dict]],
                 fx_row: dict[str, int] | None = None,
                 master: int = 100, blackout: bool = False,
                 over: dict | None = None, now: float | None = None,
                 gate_closed: int | None = 0) -> dict:
    """Final per-role values for one head.

    Intensity roles (dimmer / zone dimmer) are 0-100 after blackout and
    the grand master, and combine by HTP (highest takes the win) across
    the programmer, every active playback (scaled by that playback's
    fader) and a running effect.  Everything else is LTP: the programmer
    wins, otherwise the most recently started playback that mentions the
    role, otherwise the value is simply absent (the fixture sits at its
    own default, which the frame writer uses as 0).

    A running effect owns the roles it drives: it replaces the
    programmer/playback value, then still passes through blackout and the
    grand master like any other intensity.

    `over` is a live override from a quick button, applied on top of all
    of that and still under blackout and the master: {"level": HTP floor,
    "kill": force intensity to 0, "set": {role: value} forced LTP values,
    "strobe": Hz}.  Strobe gates the light in time when `now` is given
    (the wire); without `now` (the visualiser feed) it is left to the
    caller to show.  `gate_closed` is the value that shuts this head's
    shutter/strobe channel, or None when the profile says 0 is open and
    the gate cannot be used to black it out.
    """
    strobe_off = False
    if over and over.get("strobe") and now is not None:
        strobe_off = (now * float(over["strobe"])) % 1.0 > 0.35
    n = head["head_no"]
    pvals = prog.get(n) or {}
    fx_row = fx_row or {}
    resolved: dict[str, int] = {}
    for role in {r for r in head["map"] if r in HTP_ROLES}:
        if role in fx_row:
            total = int(fx_row[role])
        else:
            candidates = []
            value = htp_value(pvals, role)
            if value is not None:
                candidates.append(int(value))
            for level, vals in pb_vals:
                value = htp_value(vals.get(n) or {}, role)
                if value is not None:
                    candidates.append(int(value) * level // 100)
            total = max(candidates) if candidates else 0
        if over:
            if over.get("kill"):
                total = 0
            elif over.get("level") is not None:
                total = max(total, int(over["level"]))
            if strobe_off:
                total = 0
        if blackout:
            total = 0
        resolved[role] = total * master // 100
    for role in head["map"]:
        if role in HTP_ROLES or role == "unused":
            continue
        if role in fx_row:
            resolved[role] = int(fx_row[role])
            continue
        if role in pvals:
            resolved[role] = int(pvals[role])
            continue
        for _level, vals in pb_vals:
            row = vals.get(n)
            if row and role in row:
                resolved[role] = int(row[role])
                break
    if over and over.get("set"):
        for role, value in over["set"].items():
            if role in head["map"]:
                resolved[role] = int(value)
    # A fixture with no dimmer still has to obey BLACKOUT and the master:
    # its shutter/strobe gate closes (0 is closed on every profile we
    # know), and its colour channels act as a virtual dimmer.
    dimmerless = not any(r in HTP_ROLES for r in head["map"])
    killed = bool(over and (over.get("kill") or strobe_off))
    if dimmerless and (blackout or master < 100 or killed):
        scale = 0 if (blackout or killed) else master
        if scale == 0 and gate_closed is not None:
            for role in ("shutter", "strobe"):
                if role in head["map"]:
                    resolved[role] = int(gate_closed)
        for role in COLOUR_ROLES:
            if role in resolved:
                resolved[role] = resolved[role] * scale // 100
    # PER-FIXTURE LIMITS AND ORIENTATION, applied HERE and not on write.
    #
    # This is the frame boundary, which is the only place where "what the
    # operator asked for" and "what the wire needs" can both be true.  A
    # dimmer with a floor must send the floor when the value is 0, and a
    # head hung with pan and tilt the wrong way round must send the
    # opposite end - but the PROGRAMMER still holds what was typed, so the
    # encoder, the channel sheet and the console's own history all keep
    # agreeing with each other.
    #
    # Order matters: limits first, then orientation.  A limit is a fact
    # about the value; orientation is a fact about the channel, and
    # clamping an inverted pan back to the limit would quietly undo it.
    limits = head.get("limits")
    if limits:
        for role, (lo, hi) in limits.items():
            if role not in resolved:
                continue
            v = resolved[role]
            if lo is not None and v < lo:
                v = lo
            if hi is not None and v > hi:
                v = hi
            resolved[role] = v
    flags = head.get("orient")
    if flags and (flags.get("swap") or flags.get("invert_pan")
                  or flags.get("invert_tilt")):
        # Swap first, then invert, so `swap` + `invert` means the same
        # thing whichever order the operator pressed the buttons.
        if flags.get("swap"):
            p, t = resolved.get("pan"), resolved.get("tilt")
            if p is not None:
                resolved["tilt"] = p
            if t is not None:
                resolved["pan"] = t
        for role, key in (("pan", "invert_pan"), ("tilt", "invert_tilt")):
            if flags.get(key) and role in resolved:
                top = 65535 if (role + "_fine") in head["map"] else 255
                resolved[role] = top - max(0, min(top, resolved[role]))
    return resolved


def pair_map(roles: list[str]) -> tuple[dict[int, int], dict[int, int]]:
    """(base index -> fine index, fine index -> base index) for 16-bit pairs.

    A fine channel shares ONE logical value with its base channel
    (pan + pan_fine, dimmer + dimmer_fine, ...).  The pairing works in
    either physical order, so fixture definitions control channel
    ordering simply by listing the channels where the hardware wants
    them.  Fixtures without a fine channel get empty maps and keep the
    unchanged 8-bit path.
    """
    fine_of: dict[int, int] = {}
    base_of: dict[int, int] = {}
    for i, role in enumerate(roles):
        if is_fine_role(role):
            for j, other in enumerate(roles):
                if other == role[:-5]:
                    fine_of[j] = i
                    base_of[i] = j
                    break
    return fine_of, base_of


def build_frames(patch: list[dict], prog: dict,
                 pb_vals: list[tuple[int, dict]],
                 fx_vals: dict[int, dict] | None = None,
                 master: int = 100, blackout: bool = False,
                 defaults=None, overrides: dict | None = None,
                 now: float | None = None,
                 gates: dict | None = None) -> dict[int, bytearray]:
    """Merge programmer + playbacks + effects into 512-byte frames.

    `defaults` is an optional {role: value} map of what an UN-driven
    channel should sit at (from app/profiles.py).  It is per head, so the
    engine passes head["defaults"]; a head without one writes 0, which is
    the safe "nothing programmed = dark" default.
    """
    fx_vals = fx_vals or {}
    overrides = overrides or {}
    gates = gates or {}
    frames: dict[int, bytearray] = {}
    for head in patch:
        universe = head["universe"]
        buf = frames.get(universe)
        if buf is None:
            buf = frames[universe] = bytearray(SLOTS)
        offset = head["address"] - 1        # index inside this universe
        values = resolve_head(head, prog, pb_vals,
                              fx_vals.get(head["head_no"]),
                              master, blackout,
                              overrides.get(head["head_no"]), now,
                              gates.get(head["head_no"], 0))
        curve = head.get("curve", "linear")
        roles = head["map"]
        fine_of, base_of = pair_map(roles)

        def _half(idx_role: str, htp: bool) -> int:
            """16-bit logical value for one paired channel."""
            if htp:
                return _curve_pct(values.get(idx_role, 0),
                                  curve) * 65535 // 100
            return _logical16(values.get(idx_role, 0))

        for i, role in enumerate(roles):
            pos = offset + i
            if pos < 0 or pos >= SLOTS:
                continue
            partner = fine_of.get(i)
            if partner is not None:
                # one logical value -> two DMX bytes; an explicit operator
                # value on the fine channel overrides the derived half.
                fpos = offset + partner
                coarse, fine = split_16bit(_half(role, role in HTP_ROLES))
                buf[pos] = coarse
                if 0 <= fpos < SLOTS:
                    fine_role = roles[partner]
                    if fine_role in values:
                        v = values[fine_role]
                        fine = 0 if v < 0 else (255 if v > 255 else int(v))
                    buf[fpos] = fine
                continue
            if i in base_of:
                # fine channel whose base has not been written yet (base
                # sits at a higher offset): derive our byte now - the base
                # writes the same value again later.
                if role in values:
                    v = values[role]
                    buf[pos] = 0 if v < 0 else (255 if v > 255 else int(v))
                else:
                    buf[pos] = split_16bit(
                        _half(roles[base_of[i]], role[:-5] in HTP_ROLES))[1]
                continue
            if role in HTP_ROLES:
                buf[pos] = _curve_pct(values.get(role, 0), curve) * 255 // 100
            elif role != "unused":
                v = values.get(role, 0)
                buf[pos] = 0 if v < 0 else (255 if v > 255 else int(v))
    return frames
