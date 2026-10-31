"""Two (or more) people at the desk at once - a laptop and a tablet, the
operator and a programmer - each editing: the second to change the same
cue, button, palette, group, timeline or room within a few seconds of the
other is told, so one doesn't silently overwrite the other's work.

Each screen sends its own id (X-Haze-Client).  Nothing is locked or
refused - live, the operator must never be stopped - it's a heads-up.
"""
from __future__ import annotations

import threading
import time

WINDOW_S = 20.0          # another screen's edit this recent is worth a word
ACTIVE_S = 90.0          # a screen that did something this recently is "on"
_lock = threading.Lock()
_last: dict[tuple, tuple[str, float]] = {}
_seen: dict[str, float] = {}

CUES = ("record_cue", "edit_cue", "delete_cue", "insert_cue", "move_cue", "rename_cue", "cue_set")
BUTTONS = ("quick_set", "quick_edit", "quick_update", "quick_style", "quick_move", "quick_from_programmer")
PALETTES = ("record_palette", "palette_edit", "rename_preset", "record_preset", "delete_preset")
GROUPS = ("group_create", "group_edit", "group_delete")


def _key(action: str, p: dict) -> tuple | None:
    if action in CUES:
        return ("cue", str(p.get("playback") or 1), str(p.get("cue") or p.get("n") or "new"))
    if action in BUTTONS:
        return ("button", str(p.get("id") or f"{p.get('page')}:{p.get('slot')}"))
    if action in PALETTES:
        return ("palette", str(p.get("kind") or ""), str(p.get("palette") or p.get("n") or p.get("name") or ""))
    if action in GROUPS:
        return ("group", str(p.get("group") or p.get("n") or p.get("name") or ""))
    if action.startswith("timeline_") and action not in ("timeline_play", "timeline_pause", "timeline_stop", "timeline_seek"):
        return ("timeline",)
    if action.startswith("venue_") and action not in ("venue_info", "venue_preview", "venue_camera"):
        return ("room",)
    if action in ("add_heads", "remove_heads", "patch_move_free", "set_address", "change_type", "remap_heads"):
        return ("patch",)
    return None


WHAT = {"cue": "this cue", "button": "this button", "palette": "this palette", "group": "this group",
        "timeline": "the timeline", "room": "the room", "patch": "the patch"}


def note(action: str, params: dict, client: str) -> str:
    """Record an edit by this screen; a heads-up if another screen just
    edited the same thing ("" otherwise)."""
    client = str(client or "")[:40]
    now = time.monotonic()
    if client:
        with _lock:
            _seen[client] = now
    key = _key(str(action), params if isinstance(params, dict) else {})
    if key is None or not client:
        return ""
    with _lock:
        prev = _last.get(key)
        _last[key] = (client, now)
        if len(_last) > 2000:
            for k in sorted(_last, key=lambda k: _last[k][1])[:1000]:
                _last.pop(k, None)
    if prev and prev[0] != client and now - prev[1] < WINDOW_S:
        return f"Another screen changed {WHAT[key[0]]} {int(now - prev[1])} s ago - check you're not undoing each other's work."
    return ""


def screens() -> int:
    """How many screens have worked the desk in the last minute and a half."""
    now = time.monotonic()
    with _lock:
        return sum(1 for t in _seen.values() if now - t < ACTIVE_S)


def reset() -> None:
    with _lock:
        _last.clear()
        _seen.clear()
