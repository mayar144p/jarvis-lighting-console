"""The AI's hands on the whole desk (backlog A13), beyond one action at a time.

- `capabilities`: what each light CAN and CAN'T do, from its patch (the
  same facts the programmer shows), in plain words, so the AI never acts
  from its memory of a model.
- `cant_do`: after an action, which of its lights it couldn't touch and
  why ("6 x LED PAR: no pan / tilt - left as they were").
- `place_lights`: placement in words - "middle of the front truss",
  "left end", "both ends", "6 m high".
- `add_to_room`: a truss, pole, object, zone or mark in words.
- the tiers: what the AI may do (undoable, previewed), what it may only
  PREPARE for the operator to confirm with a tap (going live, arming,
  firing effects, saving / loading, every delete), and what it never does.
"""
from __future__ import annotations

import inspect
import re
import time

from . import venue as venue_mod

# -- the tiers ---------------------------------------------------------------
# More of the desk the AI may simply do: undoable, previewed in 3D first.
MORE = (
    "aim_spot", "autopilot", "autopilot_next", "clear_heads", "cue_info", "cue_set", "edit_cue",
    "floor_safe", "fx_available", "get_limits", "group_flash", "group_master", "insert_cue",
    "macro_run", "macro_save", "move_cue", "move_play", "move_range", "move_rename", "move_save",
    "nudge", "pad_info", "paperwork", "park", "patch_move_free", "place_many", "playback_mode",
    "quick_fader", "quick_from_programmer", "quick_layout", "quick_move", "quick_page", "quick_press", "quick_style",
    "quick_quant", "quick_rate", "quick_release_all", "quick_xy", "ready_check", "rename_cue",
    "rename_preset", "rig_add", "rig_pieces", "rig_report", "rig_trim", "run_media", "select_split",
    "show_versions", "sound_link", "sound_tempo", "sound_trigger", "step_capture", "step_fx_save",
    "tempo_nudge", "tempo_sync", "tempo_tap", "timeline_clip", "timeline_set", "timeline_track",
    "unpark", "venue_add", "venue_align", "venue_array", "venue_camera", "venue_ceiling",
    "venue_info", "venue_preview", "venue_rig", "venue_room", "venue_stage", "venue_update",
    "fx_kill", "colour_cal_get", "fx_status",
)
# Prepared by the AI, done only when the operator taps "Do it".
CONFIRM = {
    "set_output": "Go live: the real lights get the desk",
    "set_dry_run": "Switch the output (dry run / live)",
    "fx_arm": "ARM the special effects and lasers",
    "fx_fire": "Fire the effect (confetti / CO2 / flame / sparks)",
    "fx_fog": "Fog / haze",
    "fx_laser": "Lasers on",
    "fx_reload": "Mark the confetti refilled",
    "save_show": "Save the show",
    "show_new": "Start a new, empty show",
    "show_template": "Start a new show from a template",
    "load_show": "Open a show (this one is replaced)",
    "restore_version": "Open an earlier version of a show",
    "venue_open": "Open a saved venue (this room is replaced)",
    "venue_save": "Save this venue",
    "set_venue": "Replace the whole room",
    "venue_build": "Build a new room",
    "venue_describe": "Make the room from the description",
    "venue_shape": "Change the room's shape",
    "change_type": "Change the fixture type of lights",
    "import_show": "Make a cue stack from the design",
    "group_delete": "Delete a group",
    "delete_cue": "Delete a cue",
    "delete_preset": "Delete a preset",
    "remove_heads": "Remove lights from the patch",
    "patch_clear": "Remove every light from the patch",
    "macro_delete": "Delete a macro",
    "media_delete": "Delete a picture / clip",
    "move_delete": "Delete a saved move",
    "shape_delete": "Delete a shape",
    "step_fx_delete": "Delete a step effect",
    "venue_remove": "Remove a room item",
    "venue_delete": "Delete a saved venue",
}
# Never, whatever the request: network, the lock, calibration on the real
# light, the AI's own preview and undo (the operator has Ctrl+Z).
NEVER = frozenset({
    "set_dmx_target", "set_lock", "unlock", "set_limits", "clear_limits", "set_orient",
    "remember_open", "light_test", "light_tested", "motion_set", "motion_test",
    "motion_test_end", "motion_get", "import_scan", "patch_from_csv", "export_patch",
    "remap_heads", "osc", "virtual_node", "timecode", "tempo_prodj", "tempo_link",
    "rdm_compare", "teach_slots", "colour_cal", "media_save", "show_export",
    "quick_fx_defaults", "quick_from_laser", "blind", "undo", "redo",
})


def params_of(engine_cls, action: str) -> tuple[str, ...]:
    """An action's keyword arguments, from its engine method."""
    f = getattr(engine_cls, "_a_" + action, None)
    if f is None:
        return ()
    return tuple(p.name for p in inspect.signature(f).parameters.values()
                 if p.name not in ("self", "_") and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD))


# -- what each light can do ----------------------------------------------------
_MIX = {"red", "green", "blue", "cyan", "magenta", "yellow"}
_BEAM = (("gobo", "gobos"), ("prism", "a prism"), ("zoom", "zoom"), ("focus", "focus"),
         ("frost", "frost"), ("iris", "iris"))


def _facts(eng, h: dict) -> tuple[list[str], list[str]]:
    roles = set(h.get("map") or [])
    can, cant = [], []
    cls = eng._head_class(h)
    if cls != "light":
        return [f"an effects machine ({cls}): fires only from its armed buttons - you prepare it, the operator fires"], \
            ["colour, movement (it's not a light)"]
    if {"pan", "tilt"} <= roles:
        can.append("moves (pan + tilt)")
    elif "tilt" in roles:
        can.append("tilts only (no pan)")
        cant.append("pan")
    elif "pan" in roles:
        can.append("pans only (no tilt)")
        cant.append("tilt")
    else:
        cant.append("move (no pan / tilt: it points where it hangs)")
    if roles & _MIX:
        can.append("any colour (it mixes)")
    elif "wheel" in roles:
        slots = [s.get("name") for s in eng._wheel_slots(h) if s.get("name")]
        can.append("colour wheel only" + (f": {', '.join(slots[:12])}" if slots else ""))
        cant.append("mixed colours (only its wheel's)")
    elif "white" in roles or "amber" in roles:
        can.append("white / warm only")
        cant.append("colour")
    else:
        cant.append("colour (white only)")
    if eng._lamp_only(h):
        cant.append("dim or black out (nothing on its DMX closes it)")
    else:
        can.append("dims")
    if roles & {"shutter", "strobe"}:
        can.append("strobes")
    else:
        cant.append("strobe on its own (the desk can still flash its level)")
    beam = [word for role, word in _BEAM if role in roles]
    if beam:
        can.extend(beam)
        cant.extend(word for role, word in _BEAM if role not in roles)
    else:
        cant.append("beam effects (no gobo, prism, zoom or focus)")
    reps = max((list(h.get("map") or []).count(r) for r in ("red", "tilt")), default=1)
    if reps > 1:
        can.append(f"{reps} heads / cells each on its own")
    return can, cant


def capabilities(eng, heads=None, group=None) -> dict:
    """Per model: which lights, what they can and can't do."""
    with eng.lock:
        rows = _rows(eng, heads, group) or list(eng.patch)
        out: dict[tuple, dict] = {}
        for h in rows:
            key = (h.get("manufacturer") or "", h.get("model") or "", h.get("mode") or "")
            if key not in out:
                can, cant = _facts(eng, h)
                out[key] = {"model": f"{key[0]} {key[1]}".strip(), "mode": key[2], "heads": [],
                            "can": can, "cannot": [c for c in cant if c]}
            out[key]["heads"].append(h["head_no"])
    return {"models": list(out.values())}


def _rows(eng, heads=None, group=None) -> list[dict]:
    if group not in (None, ""):
        g = next((g for g in eng.groups if str(g.get("n")) == str(group)
                  or str(g.get("name", "")).lower() == str(group).lower()), None)
        heads = (g or {}).get("heads") or []
    if heads:
        want = {int(x) for x in heads}
        return [h for h in eng.patch if h["head_no"] in want]
    return []


# action -> what a light needs for it
_NEEDS = {
    "set_colour": "colour", "chase_colours": "colour", "run_gradient": "colour",
    "set_position": "move", "aim_at": "move", "aim_spot": "move", "roam": "move", "fan": "move",
    "run_shape": "move", "nudge": "move", "follow_set": "move", "move_play": "move",
    "set_intensity": "level",
}


def _has(eng, h: dict, need: str) -> bool:
    roles = set(h.get("map") or [])
    if need == "colour":
        return bool(roles & (_MIX | {"wheel"}))
    if need == "move":
        return bool(roles & {"pan", "tilt"})
    if need == "level":                    # as what_lights_can_do says; machines aren't lights
        return eng._head_class(h) != "light" or not eng._lamp_only(h)
    return need in roles


def cant_do(eng, action: str, params: dict) -> list[str]:
    """The lights an action couldn't touch, by model, with the reason."""
    need = _NEEDS.get(action)
    if action in ("set_attribute", "set_attr_range"):
        from .engine_base import _attr_role
        need = _attr_role(str(params.get("attribute") or params.get("role") or "")) or None
    if not need:
        return []
    with eng.lock:
        rows = _rows(eng, params.get("heads"), params.get("group")) or \
            [h for h in eng.patch if h["head_no"] in set(eng.selected)]
        missing: dict[str, list[int]] = {}
        for h in rows:
            if not _has(eng, h, need):
                missing.setdefault(f"{h.get('manufacturer') or ''} {h.get('model') or ''}".strip(), []).append(h["head_no"])
    why = {"colour": "no colour", "move": "no pan / tilt", "level": "can't dim (nothing on its DMX closes it)"}.get(need, f"no {need}")
    return [f"{len(v)} x {k} (heads {', '.join(map(str, v[:12]))}): {why} - left as they were"
            for k, v in missing.items()]


# -- placement in words ---------------------------------------------------------
def _norm(s) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower()).strip()


def find_rig(eng, words: str) -> dict | None:
    """A rig by its id, its name, or where it is ("front truss", "the left pole")."""
    rigs = venue_mod.normalise(eng.venue).get("rigging") or []
    w = _norm(words)
    if not rigs or not w:
        return None
    for r in rigs:
        if r["id"] == str(words).strip() or _norm(r.get("name")) == w:
            return r
    named = [r for r in rigs if r.get("name") and all(t in _norm(r["name"]) for t in w.split()
                                                     if t not in ("the", "on", "truss", "rig", "bar"))]
    if len(named) == 1:
        return named[0]
    pool = named or rigs
    kinds = [k for k in ("truss", "pipe", "tower", "ladder", "stand", "base") if k in w.split()]
    if "pole" in w.split():
        kinds += ["tower", "stand"]
    if kinds:
        pool = [r for r in pool if r["kind"] in kinds] or pool
    mid = lambda r, i: (r["a"][i] + r["b"][i]) / 2          # noqa: E731
    # the stage is at the back wall (low z): "upstage" is nearest it, the
    # "front" truss the next one out over the floor, "rear" the farthest
    flat = sorted((r for r in pool if not venue_mod.is_vertical(r)), key=lambda r: mid(r, 2)) or pool
    words = w.split()
    if "upstage" in words:
        return flat[0]
    if "front" in words or "downstage" in words:
        return flat[1] if len(flat) > 1 else flat[0]
    if "rear" in words or "back" in words:
        return flat[-1]
    order = {"left": lambda r: mid(r, 0), "right": lambda r: -mid(r, 0),
             "high": lambda r: -mid(r, 1), "top": lambda r: -mid(r, 1), "low": lambda r: mid(r, 1)}
    for word, key in order.items():
        if word in words:
            return min(pool, key=key)
    if "middle" in w.split() or "centre" in w.split() or "center" in w.split():
        return min(pool, key=lambda r: abs(mid(r, 0)) + abs(mid(r, 2)))
    return pool[0] if len(pool) == 1 else None


def spots(where: str, n: int, rig: dict) -> list[float] | None:
    """Points along a rig (0..1, from its left end) for n lights."""
    w = _norm(where)
    left_first = rig["a"][0] <= rig["b"][0]          # t=0 is the left end
    flip = (lambda t: t) if left_first else (lambda t: 1 - t)
    gap = 0.7 / max(0.5, venue_mod.length(rig))
    try:
        t = float(w)
        if 0 <= t <= 1:
            return [round(min(1, max(0, flip(t) + (i - (n - 1) / 2) * gap)), 3) for i in range(n)]
    except ValueError:
        pass
    if "end" in w and ("both" in w or "each" in w or "ends" in w):
        return [flip(0.04 if i % 2 == 0 else 0.96) for i in range(n)]
    if "spread" in w or "even" in w or "across" in w or "along" in w:
        return [flip((i + 0.5) / n) for i in range(n)]
    for word, t in (("left", 0.06), ("right", 0.94)):
        if word in w:
            return [round(min(1, max(0, flip(t + (i * gap if t < .5 else -i * gap)))), 3) for i in range(n)]
    if not w or any(x in w for x in ("middle", "centre", "center", "mid")):
        return [round(min(1, max(0, 0.5 + (i - (n - 1) / 2) * gap)), 3) for i in range(n)]
    return None


def place_lights(eng, heads=None, group=None, on="", where="middle", height=None) -> dict:
    """Hang lights on a rig at a place in words, and/or at a height."""
    rows = _rows(eng, heads, group) or [h for h in eng.patch if h["head_no"] in set(eng.selected)]
    if not rows:
        return {"ok": False, "error": "say which lights (heads or a group), or select them first"}
    nums = [h["head_no"] for h in rows]
    done = []
    if on:
        rig = find_rig(eng, on)
        if rig is None:
            rigs = venue_mod.normalise(eng.venue).get("rigging") or []
            return {"ok": False, "error": f"no rig matches {on!r}",
                    "rigs": [f"{r['id']} {r.get('name') or r['kind']}" for r in rigs]}
        ts = spots(where, len(nums), rig)
        if ts is None:
            return {"ok": False, "error": f"where on it? (middle, left, right, both ends, spread, or 0-1) - not {where!r}"}
        for n, t in zip(nums, ts):
            r = eng.act("set_place", head=n, rig=rig["id"], t=t)
            if not r.get("ok"):
                return {"ok": False, "error": r.get("error")}
        done.append(f"{len(nums)} light(s) on {rig.get('name') or rig['id']} ({where or 'middle'})")
        if height is not None:
            r = eng.act("rig_trim", id=rig["id"], trim=float(height))
            if not r.get("ok"):
                return {"ok": False, "error": r.get("error")}
            done.append(f"{rig.get('name') or rig['id']} hung at {float(height):g} m")
    elif height is not None:
        for h in rows:
            r = eng.act("set_place", head=h["head_no"], x=h.get("x"), y=float(height), z=h.get("z"), rig="")
            if not r.get("ok"):
                return {"ok": False, "error": r.get("error")}
        done.append(f"{len(nums)} light(s) at {float(height):g} m")
    else:
        return {"ok": False, "error": "say where: on a rig (on=...) and/or a height"}
    return {"ok": True, "summary": "; ".join(done), "heads": nums}


# -- the room in words -----------------------------------------------------------
def room_spot(eng, where: str) -> tuple[float, float]:
    """x, z of a place in words: front, back, left, right, middle, front left..."""
    v = venue_mod.normalise(eng.venue)
    r = v["room"]
    cx, back, w, d = r.get("cx") or 0.0, r.get("back", -1.0), r["width"], r["depth"]
    words = _norm(where).split()
    x, z = cx, back + d / 2
    if "left" in words:
        x = cx - w * 0.3
    if "right" in words:
        x = cx + w * 0.3
    # the stage end of the room is its front (low z); the back is by the bar / doors
    if any(t in words for t in ("front", "stage", "upstage", "downstage")):
        z = back + d * 0.15
    if any(t in words for t in ("back", "rear", "door", "doors", "bar")):
        z = back + d * 0.85
    return round(x, 2), round(z, 2)


_RIG_ALIAS = {"pole": "tower", "post": "tower", "goalpost": "truss", "bar": "pipe"}


def add_to_room(eng, kind: str, name: str = "", where: str = "middle", length=None, width=None,
                depth=None, height=None, across: bool = True) -> dict:
    """A truss, pole, object, zone or mark, placed in words."""
    k = _norm(kind).replace(" ", "_")
    k = _RIG_ALIAS.get(k, k)
    x, z = room_spot(eng, where)
    if k in venue_mod.RIG_KINDS:
        if k in ("tower", "ladder", "stand", "base"):
            item = {"kind": k, "a": [x, 0, z], "b": [x, float(height or 3.0), z]}
        else:
            half = float(length or 6.0) / 2
            y = float(height or 5.0)
            item = {"kind": k, "a": [x - half, y, z], "b": [x + half, y, z]} if across else \
                {"kind": k, "a": [x, y, z - half], "b": [x, y, z + half]}
    elif k in venue_mod.OBJECT_KINDS:
        item = {"kind": k, "x": x, "z": z, "w": float(width or (0.4 if k == "mark" else 2.0)),
                "d": float(depth or (0.4 if k == "mark" else 1.0)), "h": float(height or (0.02 if k == "mark" else 1.0))}
    elif k in venue_mod.ZONE_KINDS or k == "zone":
        hw, hd = float(width or 4.0) / 2, float(depth or 4.0) / 2
        item = {"kind": k if k != "zone" else "standing",
                "points": [[x - hw, z - hd], [x + hw, z - hd], [x + hw, z + hd], [x - hw, z + hd]]}
    else:
        return {"ok": False, "error": f"no room item {kind!r}",
                "kinds": list(venue_mod.RIG_KINDS) + ["pole"] + list(venue_mod.OBJECT_KINDS) + list(venue_mod.ZONE_KINDS)}
    if name:
        item["name"] = str(name)[:40]
    r = eng.act("venue_add", item=item)
    return {"ok": bool(r.get("ok")), "summary": r.get("summary") or r.get("error"), "id": r.get("id"),
            **({"error": r.get("error")} if not r.get("ok") else {})}


# -- confirmations ----------------------------------------------------------------
_waiting: dict[str, dict] = {}
WAIT_S = 600.0


def prepare(action: str, params: dict) -> dict:
    """Hold an action for the operator's tap; returns what to show."""
    import os
    now = time.monotonic()
    for k in [k for k, v in _waiting.items() if now - v["at"] > WAIT_S]:
        _waiting.pop(k, None)
    cid = os.urandom(6).hex()
    detail = ", ".join(f"{k} {v}" for k, v in params.items() if v not in (None, "", [], {}))[:120]
    label = CONFIRM[action] + (f" ({detail})" if detail else "")
    _waiting[cid] = {"action": action, "params": params, "label": label, "at": now}
    return {"id": cid, "label": label, "action": action}


def confirm(eng, cid: str, yes: bool = True) -> dict:
    """The operator's tap: do it (or don't)."""
    item = _waiting.pop(str(cid or ""), None)
    if item is None:
        return {"ok": False, "error": "that's no longer waiting - ask again"}
    if not yes:
        return {"ok": True, "summary": f"not done: {item['label']}"}
    r = eng.act(item["action"], **item["params"])
    return {"ok": bool(r.get("ok")), "summary": r.get("summary") or r.get("error") or item["label"],
            **({"error": r.get("error")} if not r.get("ok") else {})}
