"""Songs from a rekordbox USB stick: the parts rekordbox found in each track
(its "phrase analysis": Intro, Up, Down, Chorus, Outro...), the beat grid
and the file's name - so a song's show can be designed with no music file,
from the same analysis the CDJs show the DJ.

rekordbox writes an analysis file pair for every track it exports:
PIONEER/USBANLZ/Pxxx/xxxxxxxx/ANLZ0000.DAT and .EXT.  Both are a header
("PMAI") then tagged sections, big-endian.  The ones read here:

    PPTH (.DAT)   the track's file path (UTF-16)
    PQTZ (.DAT)   the beat grid: per beat its number in the bar (1-4),
                  the tempo (BPM x 100) and the time (ms)
    PSSI (.EXT)   the phrases: the track's mood (high / mid / low) and
                  per phrase its first beat and kind; the end beat.  Newer
                  rekordbox versions scramble its body with an XOR mask
                  (undone here).

The layout follows Deep Symmetry's published analysis of the format
(crate-digger, "rekordbox_anlz"); no Pioneer code or library is used.
The browser picks the stick's folder and sends only these three tags, so
a big USB stick isn't uploaded whole.
"""
from __future__ import annotations

import base64
import re
import struct

TAGS = ("PPTH", "PQTZ", "PSSI")
MASK = bytes([0xCB, 0xE1, 0xEE, 0xFA, 0xE5, 0xEE, 0xAD, 0xEE, 0xE9, 0xD2,
              0xE9, 0xEB, 0xE1, 0xE9, 0xF3, 0xE8, 0xE9, 0xF4, 0xE1])

MOODS = {1: "high", 2: "mid", 3: "low"}
# a phrase's kind, by the track's mood, in the words the show designer knows
HIGH = {1: "Intro", 2: "Build", 3: "Breakdown", 5: "Drop", 6: "Outro"}           # rekordbox: Up, Down, Chorus
MID = {1: "Intro", 2: "Verse", 3: "Verse", 4: "Verse", 5: "Verse", 6: "Verse", 7: "Verse",
       8: "Bridge", 9: "Chorus", 10: "Outro"}
LOW = {1: "Intro", 2: "Verse", 3: "Verse", 4: "Verse", 5: "Verse", 6: "Verse", 7: "Verse",
       8: "Bridge", 9: "Chorus", 10: "Outro"}
MOOD_WORDS = {"high": "a high-energy track (rekordbox: up, down and chorus phrases - builds and drops)",
              "mid": "a mid-energy track (rekordbox: verses, bridge and chorus)",
              "low": "a low-energy track (rekordbox: verses, bridge and chorus)"}


def read_tags(data: bytes, want=TAGS) -> dict[str, bytes]:
    """The tagged sections of an analysis file (.DAT / .EXT): {fourcc: the
    whole tag, header included}.  A damaged file gives what could be read."""
    out: dict[str, bytes] = {}
    if len(data) < 12 or data[:4] != b"PMAI":
        raise ValueError("not a rekordbox analysis file")
    pos = struct.unpack(">I", data[4:8])[0]
    while pos + 12 <= len(data):
        kind = data[pos:pos + 4].decode("latin-1")
        size = struct.unpack(">I", data[pos + 8:pos + 12])[0]
        if size < 12 or pos + size > len(data):
            break
        if kind in want and kind not in out:
            out[kind] = data[pos:pos + size]
        pos += size
    return out


def _path(tag: bytes) -> str:
    head = struct.unpack(">I", tag[4:8])[0]
    n = struct.unpack(">I", tag[12:16])[0]
    raw = tag[head:head + n]
    return raw.decode("utf-16-be", "replace").rstrip("\x00")


def _beats(tag: bytes) -> list[tuple[int, float, float]]:
    """[(beat in bar, bpm, seconds)] from a PQTZ tag."""
    head = struct.unpack(">I", tag[4:8])[0]
    n = struct.unpack(">I", tag[20:24])[0]
    out = []
    for i in range(n):
        at = head + i * 8
        if at + 8 > len(tag):
            break
        num, tempo, ms = struct.unpack(">HHI", tag[at:at + 8])
        out.append((num, tempo / 100.0, ms / 1000.0))
    return out


def _phrases(tag: bytes) -> tuple[str, int, list[tuple[int, int]]]:
    """(mood, end beat, [(first beat, kind)]) from a PSSI tag."""
    entry = struct.unpack(">I", tag[12:16])[0] or 24
    count = struct.unpack(">H", tag[16:18])[0]
    body = bytearray(tag[18:])
    raw_mood = struct.unpack(">H", bytes(body[0:2]))[0] if len(body) >= 2 else 0
    if raw_mood > 20:                     # scrambled (rekordbox 6 and later)
        mask = bytes((b + count) & 0xFF for b in MASK)
        for i in range(len(body)):
            body[i] ^= mask[i % len(mask)]
    mood = MOODS.get(struct.unpack(">H", bytes(body[0:2]))[0], "high")
    end_beat = struct.unpack(">H", bytes(body[8:10]))[0]
    phrases = []
    for i in range(count):
        at = 14 + i * entry
        if at + 6 > len(body):
            break
        _index, beat, kind = struct.unpack(">HHH", bytes(body[at:at + 6]))
        phrases.append((beat, kind))
    return mood, end_beat, phrases


def track(tags: dict[str, bytes]) -> dict:
    """One track's song: {name, bpm, length, first_beat, mood, notes, markers}.
    Needs the beat grid and the phrases; the path gives the name."""
    if "PQTZ" not in tags or "PSSI" not in tags:
        raise ValueError("no phrase analysis for this track (analyse it in rekordbox with phrases on)")
    beats = _beats(tags["PQTZ"])
    if len(beats) < 8:
        raise ValueError("no beat grid for this track")
    mood, end_beat, phrases = _phrases(tags["PSSI"])
    if len(phrases) < 2:
        raise ValueError("rekordbox found no phrases in this track")
    names = {"high": HIGH, "mid": MID, "low": LOW}[mood]

    def at(beat: int) -> float:
        i = max(1, min(len(beats), beat)) - 1
        if beat > len(beats):                     # past the grid: on at the last tempo
            n, bpm, t = beats[-1]
            return t + (beat - len(beats)) * 60.0 / (bpm or 120.0)
        return beats[i][2]

    markers, seen = [], {}
    for beat, kind in sorted(phrases):
        name = names.get(kind, "Groove")
        if markers and markers[-1]["kind"] == name:
            continue                               # two phrases of one kind in a row: one part
        seen[name] = seen.get(name, 0) + 1
        markers.append({"kind": name, "t": round(at(beat), 3),
                        "name": name if seen[name] == 1 else f"{name} {seen[name]}"})
    tempos = sorted(b[1] for b in beats if b[1] > 0)
    bpm = tempos[len(tempos) // 2] if tempos else 120.0
    length = at(end_beat) if end_beat else beats[-1][2] + 60.0 / bpm
    path = _path(tags["PPTH"]) if "PPTH" in tags else ""
    name = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", path.replace("\\", "/").rsplit("/", 1)[-1]).strip() or "Track"
    return {"name": name[:60], "file": path[:200], "bpm": round(bpm, 2), "length": round(max(length, markers[-1]["t"] + 1), 2),
            "first_beat": round(beats[0][2], 3), "mood": mood, "notes": MOOD_WORDS[mood],
            "markers": [{"t": m["t"], "name": m["name"]} for m in markers]}


def from_upload(item: dict) -> dict:
    """A track as the browser sends it: {tags: {fourcc: base64}}."""
    raw = item.get("tags") if isinstance(item, dict) else None
    if not isinstance(raw, dict):
        raise ValueError("no analysis tags")
    tags = {}
    for k, v in raw.items():
        if k in TAGS and isinstance(v, str) and len(v) < 4_000_000:
            tags[k] = base64.b64decode(v)
    return track(tags)
