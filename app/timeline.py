"""The show timeline: tracks of timed clips, like a DAW or a show-control
timeline.

    cue     GO a playback to a cue at a moment
    button  hold a quick button for a clip's length (strobe hits, flashes)
    fx      run an effect on a group or type for a clip's length
    level   keyframed fader automation for a playback or the grand master

Pure data here (cleaning, lookups, interpolation, building clips from a
cue list); the engine owns the clock and fires the events.
"""
from __future__ import annotations

import copy
import math

TRACK_KINDS = ("cue", "button", "fx", "level")
MAX_LENGTH = 6 * 3600.0


def _num(v, fallback=0.0, lo=None, hi=None) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float(fallback)
    if math.isnan(x) or math.isinf(x):
        return float(fallback)
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x


def empty() -> dict:
    return {"length": 120.0, "bpm": 120.0, "loop": False, "audio": None,
            "markers": [], "tracks": [], "seq": 0}


def _next_id(doc: dict, prefix: str) -> str:
    doc["seq"] = int(doc.get("seq") or 0) + 1
    return f"{prefix}{doc['seq']}"


def _clean_target(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("group") is not None:
        return {"group": int(_num(raw["group"], 1, 1))}
    if raw.get("type"):
        return {"type": str(raw["type"])[:30]}
    if raw.get("heads"):
        return {"heads": sorted({int(h) for h in raw["heads"]})[:512]}
    return {"all": True}


def clean_clip(kind: str, raw: dict, doc: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    clip = {"id": str(raw.get("id") or "")[:24] or _next_id(doc, "c"),
            "t": round(_num(raw.get("t"), 0, 0, MAX_LENGTH), 3)}
    if kind == "cue":
        cue = raw.get("cue")
        clip["cue"] = int(_num(cue, 1, 1)) if cue not in (None, "", "next") else "next"
    elif kind == "button":
        if not raw.get("button"):
            return None
        clip["button"] = str(raw["button"])[:16]
        clip["dur"] = round(_num(raw.get("dur"), 0.5, 0.05, MAX_LENGTH), 3)
    elif kind == "fx":
        if not raw.get("fx"):
            return None
        clip["fx"] = str(raw["fx"])[:30]
        clip["dur"] = round(_num(raw.get("dur"), 4.0, 0.1, MAX_LENGTH), 3)
        clip["target"] = _clean_target(raw.get("target"))
    elif kind == "level":
        clip["v"] = round(_num(raw.get("v"), 100, 0, 100), 2)
    if raw.get("label"):
        clip["label"] = str(raw["label"])[:40]
    return clip


def clean_track(raw: dict, doc: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").lower()
    if kind not in TRACK_KINDS:
        return None
    track = {"id": str(raw.get("id") or "")[:24] or _next_id(doc, "t"),
             "kind": kind, "name": str(raw.get("name") or kind.title())[:30],
             "mute": bool(raw.get("mute"))}
    if kind in ("cue",):
        track["playback"] = int(_num(raw.get("playback"), 1, 1, 64))
    if kind == "level":
        target = str(raw.get("target") or "pb1")
        track["target"] = target if target == "master" or (
            target.startswith("pb") and target[2:].isdigit()) else "pb1"
    clips = []
    seen = set()
    for c in raw.get("clips") or []:
        clip = clean_clip(kind, c, doc)
        if clip and clip["id"] not in seen:
            seen.add(clip["id"])
            clips.append(clip)
    track["clips"] = sorted(clips, key=lambda c: c["t"])
    return track


def normalise(raw) -> dict:
    if not isinstance(raw, dict):
        return empty()
    doc = empty()
    doc["seq"] = int(_num(raw.get("seq"), 0, 0))
    doc["length"] = round(_num(raw.get("length"), 120, 1, MAX_LENGTH), 3)
    doc["bpm"] = round(_num(raw.get("bpm"), 120, 20, 300), 2)
    doc["loop"] = bool(raw.get("loop"))
    a = raw.get("audio")
    if isinstance(a, dict) and a.get("id"):
        doc["audio"] = {"id": "".join(ch for ch in str(a["id"]) if ch.isalnum())[:64],
                        "name": str(a.get("name") or "audio")[:80],
                        "duration": round(_num(a.get("duration"), 0, 0, MAX_LENGTH), 3),
                        "offset": round(_num(a.get("offset"), 0, -MAX_LENGTH, MAX_LENGTH), 3)}
    doc["markers"] = sorted(
        [{"t": round(_num(m.get("t"), 0, 0, MAX_LENGTH), 3), "name": str(m.get("name") or "")[:30]}
         for m in raw.get("markers") or [] if isinstance(m, dict)], key=lambda m: m["t"])[:200]
    ids = set()
    for t in raw.get("tracks") or []:
        track = clean_track(t, doc)
        if not track:
            continue
        if track["id"] in ids:
            track["id"] = _next_id(doc, "t")
        ids.add(track["id"])
        doc["tracks"].append(track)
    doc["tracks"] = doc["tracks"][:64]
    return doc


def track(doc: dict, ident: str) -> dict | None:
    return next((t for t in doc.get("tracks") or [] if t["id"] == ident), None)


def find_clip(doc: dict, ident: str) -> tuple[dict, dict] | None:
    for t in doc.get("tracks") or []:
        for c in t["clips"]:
            if c["id"] == ident:
                return t, c
    return None


def level_at(track_: dict, pos: float) -> float | None:
    """Keyframed value at `pos` (linear between keys, held at the ends)."""
    keys = track_.get("clips") or []
    if not keys:
        return None
    if pos <= keys[0]["t"]:
        return keys[0]["v"]
    for a, b in zip(keys, keys[1:]):
        if a["t"] <= pos <= b["t"]:
            span = b["t"] - a["t"]
            k = 0.0 if span <= 0 else (pos - a["t"]) / span
            return a["v"] + (b["v"] - a["v"]) * k
    return keys[-1]["v"]


def last_cue_before(track_: dict, pos: float) -> dict | None:
    best = None
    for c in track_.get("clips") or []:
        if c["t"] <= pos + 1e-6:
            best = c
    return best


def spans_at(track_: dict, pos: float) -> list[dict]:
    return [c for c in track_.get("clips") or []
            if c["t"] <= pos < c["t"] + c.get("dur", 0)]


def end_time(doc: dict) -> float:
    end = 0.0
    for t in doc.get("tracks") or []:
        for c in t["clips"]:
            end = max(end, c["t"] + c.get("dur", 0.0))
    a = doc.get("audio")
    if a:
        end = max(end, a["duration"] + a.get("offset", 0.0))
    return end


def clips_from_stack(stack: list[dict], start: float = 0.0,
                     default_wait: float = 4.0) -> list[dict]:
    """A cue list laid out in time: each cue starts after the one before
    has faded, held and (when it follows) waited."""
    out, t = [], start
    for cue in stack:
        out.append({"t": round(t, 3), "cue": cue["n"],
                    "label": cue.get("name") or f"Cue {cue['n']}"})
        fade = float(cue.get("fade_s") or 0)
        hold = float(cue.get("hold_s") or 0)
        follow = cue.get("follow_s")
        wait = float(follow) if isinstance(follow, (int, float)) and follow > 0 else default_wait
        t += max(0.5, fade + hold + wait)
    return out


def with_track(doc: dict, raw: dict) -> tuple[dict, dict]:
    doc = normalise(copy.deepcopy(doc))
    raw = dict(raw or {})
    raw.pop("id", None)
    t = clean_track(raw, doc)
    if not t:
        raise ValueError(f"track kind must be one of {', '.join(TRACK_KINDS)}")
    doc["tracks"].append(t)
    return doc, t
