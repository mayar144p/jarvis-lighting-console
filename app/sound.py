"""Sound-reactive control: what the room sounds like, turned into light.

The browser listens (Web Audio: a microphone or the line in) and sends,
about 25 times a second, how loud the room is overall and in three bands
(bass, mid, high; 0..1 after its own automatic gain), plus events: a
beat, a drop (the bass coming back hard after a breakdown), and its
tempo estimate.  The desk keeps the latest reading and:

* **links** - a sound source (level, bass, mid, high, or the beat as a
  pulse that decays) moves a target: the whole rig's brightness, a
  group's, some lights', or the speed of the running effects.  `depth`
  is how much it moves (100: from dark to full with the music; 30: a
  gentle breathing), `gain` how sensitive it is.
* **triggers** - on a beat, the first beat of a bar, or a drop, press a
  button (every Nth time).

A reading older than STALE_S counts as no sound at all, and then no link
does anything: unplugging the microphone never leaves the rig dark.

Pure: the engine passes the time and its state in.
"""
from __future__ import annotations

import math

SOURCES = ("level", "bass", "mid", "high", "beat")
TARGETS = ("master", "group", "heads", "fx_speed")
EVENTS = ("beat", "bar", "drop")
STALE_S = 0.6
BEAT_DECAY_S = 0.18
MAX_LINKS = 16
MAX_TRIGGERS = 16


def _f(v, default, lo, hi) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(x) or math.isinf(x):
        return float(default)
    return max(lo, min(hi, x))


def empty_config() -> dict:
    return {"links": [], "triggers": [], "tempo": False}


def clean_link(raw: dict, ident: str) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    source = str(raw.get("source") or "level").lower()
    if source not in SOURCES:
        raise ValueError(f"source is one of {', '.join(SOURCES)}")
    t = raw.get("target") if isinstance(raw.get("target"), dict) else {"type": raw.get("target") or "master"}
    ttype = str(t.get("type") or "master").lower()
    if ttype not in TARGETS:
        raise ValueError(f"target is one of {', '.join(TARGETS)}")
    target = {"type": ttype}
    if ttype == "group":
        if not t.get("group"):
            raise ValueError("which group?")
        target["group"] = int(t["group"])
    if ttype == "heads":
        heads = [int(n) for n in (t.get("heads") or []) if str(n).lstrip("-").isdigit()]
        if not heads:
            raise ValueError("which lights?")
        target["heads"] = heads[:512]
    return {"id": ident, "source": source, "target": target,
            "depth": round(_f(raw.get("depth"), 60, 0, 100)),
            "gain": round(_f(raw.get("gain"), 1.0, 0.25, 4.0), 2),
            "on": raw.get("on") is not False,
            "name": str(raw.get("name") or "")[:30]}


def clean_trigger(raw: dict, ident: str) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    on = str(raw.get("on") or "beat").lower()
    if on not in EVENTS:
        raise ValueError(f"on is one of {', '.join(EVENTS)}")
    if not raw.get("button"):
        raise ValueError("which button?")
    return {"id": ident, "on": on, "button": str(raw["button"])[:20],
            "every": int(_f(raw.get("every"), 1, 1, 64)), "enabled": raw.get("enabled") is not False}


def clean_config(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = empty_config()
    for i, lk in enumerate(raw.get("links") or []):
        try:
            out["links"].append(clean_link(lk, str((lk or {}).get("id") or f"s{i + 1}")))
        except (ValueError, TypeError):
            continue
    for i, tr in enumerate(raw.get("triggers") or []):
        try:
            out["triggers"].append(clean_trigger(tr, str((tr or {}).get("id") or f"t{i + 1}")))
        except (ValueError, TypeError):
            continue
    out["links"] = out["links"][:MAX_LINKS]
    out["triggers"] = out["triggers"][:MAX_TRIGGERS]
    out["tempo"] = bool(raw.get("tempo"))
    return out


def clean_reading(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {k: round(_f(raw.get(k), 0, 0, 1), 3) for k in ("level", "bass", "mid", "high")}
    out["beat"] = bool(raw.get("beat"))
    out["drop"] = bool(raw.get("drop"))
    bpm = raw.get("bpm")
    out["bpm"] = round(_f(bpm, 0, 0, 300), 1) if bpm else None
    out["confidence"] = round(_f(raw.get("confidence"), 0, 0, 1), 2)
    return out


def value(source: str, reading: dict | None, beat_at: float | None, now: float) -> float:
    """0..1 for one source right now (0 when there is no sound)."""
    if not reading:
        return 0.0
    if source == "beat":
        if beat_at is None:
            return 0.0
        return math.exp(-max(0.0, now - beat_at) / BEAT_DECAY_S)
    return float(reading.get(source) or 0.0)


def link_factor(link: dict, v: float) -> float:
    """0..1: how much of its brightness (or 0..2x its speed) the target keeps."""
    x = max(0.0, min(1.0, v * link["gain"]))
    d = link["depth"] / 100.0
    if link["target"]["type"] == "fx_speed":
        return max(0.05, 1.0 + d * (2.0 * x - 1.0))        # 1-d .. 1+d
    return (1.0 - d) + d * x                                # 1-d .. 1


def apply(cfg: dict, reading: dict | None, beat_at: float | None, now: float,
          light_heads: list[int], groups: dict) -> tuple[dict[int, float], float]:
    """({head: brightness factor 0..1}, effect speed factor) from the links."""
    scales: dict[int, float] = {}
    speed = 1.0
    if not reading:
        return scales, speed
    for lk in cfg.get("links") or []:
        if not lk.get("on"):
            continue
        f = link_factor(lk, value(lk["source"], reading, beat_at, now))
        t = lk["target"]
        if t["type"] == "fx_speed":
            speed *= f
            continue
        if t["type"] == "master":
            heads = light_heads
        elif t["type"] == "group":
            heads = list((groups.get(t["group"]) or {}).get("heads") or [])
        else:
            heads = t["heads"]
        for n in heads:
            scales[n] = scales.get(n, 1.0) * f
    return scales, speed
