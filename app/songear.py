"""The AI listens to the song on the timeline and marks its parts.

The desk's own part finder (the Timeline's "Find the song's parts") reads
loudness; it can't tell a false build-up from a real one, or a breakdown
from a quiet verse.  Gemini hears the music: it marks intro, verse, build,
false build, drop, breakdown, outro, with how much energy each has - so the
show designer can hold the hit for the real drop.

Online only (the offline AI can't hear); mp3 and wav up to 14 MB (a long
song: export it shorter, or mark it by hand).
"""
from __future__ import annotations

import base64

from . import config, llm

AUDIO_EXT = (".mp3", ".wav")
MAX_BYTES = 14 * 1024 * 1024
PARTS = ("intro", "verse", "chorus", "build", "false build", "drop", "breakdown", "bridge", "outro")

PROMPT = """Listen to this whole track as a club lighting designer would, and mark its parts.
For each part: when it starts (seconds from the start), what it is - one of intro, verse,
chorus, build, false build (a build-up that does NOT lead to a drop), drop, breakdown, bridge,
outro - and its energy 1-5.  Mark the moment each drop actually hits, not the build before it.
Also the tempo (BPM) and one line on the track's feel (for colours and movement)."""

SCHEMA = {"type": "object", "properties": {
    "bpm": {"type": "number"},
    "feel": {"type": "string"},
    "parts": {"type": "array", "items": {"type": "object", "properties": {
        "start": {"type": "number"}, "kind": {"type": "string", "enum": list(PARTS)},
        "energy": {"type": "integer", "minimum": 1, "maximum": 5}},
        "required": ["start", "kind"]}}},
    "required": ["parts"]}


def audio_file(eng):
    a = (eng.timeline or {}).get("audio") or {}
    ident = "".join(ch for ch in str(a.get("id") or "") if ch.isalnum())[:64]
    if not ident:
        return None
    for ext in AUDIO_EXT:
        p = config.DATA / "audio" / (ident + ext)
        if p.is_file():
            return p
    return None


def parts_to_markers(raw: dict, length: float) -> list[dict]:
    """The AI's parts as timeline markers: in order, inside the song, named
    ("Drop 2", "False build"), at least two seconds apart."""
    out: list[dict] = []
    count: dict[str, int] = {}
    for p in sorted((p for p in raw.get("parts") or [] if isinstance(p, dict)), key=lambda p: float(p.get("start") or 0)):
        try:
            t = round(max(0.0, float(p.get("start") or 0)), 2)
        except (TypeError, ValueError):
            continue
        kind = str(p.get("kind") or "").lower()
        if kind not in PARTS or (length and t >= length) or (out and t - out[-1]["t"] < 2.0):
            continue
        count[kind] = count.get(kind, 0) + 1
        name = kind.capitalize() + (f" {count[kind]}" if kind in ("drop", "build", "breakdown", "verse", "chorus") else "")
        energy = p.get("energy")
        out.append({"t": t, "name": name + (f" ({int(energy)}/5)" if isinstance(energy, (int, float)) else "")})
    return out


def listen(eng, apply: bool = True) -> dict:
    """Hear the timeline's song; mark its parts (one undo step)."""
    path = audio_file(eng)
    if path is None:
        return {"ok": False, "error": "there's no song on the timeline to listen to (mp3 or wav) - load one in the Timeline"}
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        return {"ok": False, "error": "the song file is too big to send to the AI (over 14 MB) - mark its parts in the Timeline"}
    try:
        raw = llm.hear(PROMPT, base64.b64encode(data).decode("ascii"), path.suffix.lstrip("."),
                       "song_parts", "The song's parts, tempo and feel.", SCHEMA)
    except llm.LLMError as exc:
        return {"ok": False, "error": str(exc)}
    length = float((eng.timeline or {}).get("length") or 0)
    marks = parts_to_markers(raw, length)
    if len(marks) < 2:
        return {"ok": False, "error": "the AI couldn't hear clear parts in this song - mark them in the Timeline"}
    out = {"ok": True, "markers": marks, "feel": str(raw.get("feel") or "")[:200],
           "summary": f"{len(marks)} parts: " + ", ".join(m["name"] for m in marks)}
    bpm = raw.get("bpm")
    if isinstance(bpm, (int, float)) and 60 <= bpm <= 200:
        out["bpm"] = round(float(bpm), 1)
    if apply:
        r = eng.act("timeline_set", markers=marks, **({"bpm": out["bpm"]} if "bpm" in out else {}))
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or "the parts couldn't be marked"}
    return out
