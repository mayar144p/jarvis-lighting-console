"""What the AI learns about this operator - kept so it can't learn the wrong
thing quietly, and so anything it learnt can be seen and taken back.

The rules (from how other apps keep an AI's memory honest: ChatGPT's and
Seeq's reviewable memories, the "memory poisoning" guidance):

  * Every note says where it came from: YOU asked ("remember: warm white
    for speeches"), or the AI NOTICED something and suggests it.
  * What the AI only noticed waits for your OK ("Keep it?"), and a
    suggestion nobody answers is dropped after two weeks.  Only kept notes
    reach the AI.
  * A note that says much the same as an older one replaces it instead of
    piling up beside it (so "strobes on the drops" and "no strobes" don't
    both stand).
  * Habits learnt from your edits to the AI's shows are worked out LIVE from
    the shows as they are now - never written down - so undoing an edit, or
    deleting the show, unlearns it.  A habit you don't want: Forget it, and
    it stays forgotten.
  * Learning can be paused (Settings -> AI): nothing new is learnt; what's
    kept still applies.
  * Your own notes are never pushed out by anything the desk learnt.

Kept in DATA/assistant_memory.json (this computer, never in a show file).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time

from . import config

MAX_NOTES = 40
PENDING_DAYS = 14
SIMILAR = 0.5                 # word overlap at which a new note replaces an old one
_lock = threading.Lock()
# a request in these words means the operator asked for it to be remembered
ASKED = re.compile(r"\b(remember|from now on|always|never|every time|in future|i (?:like|prefer|want|hate|don'?t like)|"
                   r"don'?t ever|make sure you)\b", re.I)
# an auto-learnt line from before (it's worked out live now)
OLD_AUTO = re.compile(r"^(Habit \(in |For the .+ show only: )")
_STOP = frozenset("a an the and or to of on in at for with me my it this that is are be do make set some all "
                  "please can you i want would like just them they".split())


def _path():
    return config.DATA / "assistant_memory.json"


def _load() -> dict:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    out = {"notes": [], "dismissed_habits": [], "dismissed_undos": [], "paused": bool(raw.get("paused"))}
    for n in raw.get("notes") or []:
        if isinstance(n, str):                       # the first version: plain text
            if OLD_AUTO.match(n):
                continue                             # (learnt live now - see learning())
            n = {"text": n, "source": "you", "status": "kept", "at": 0}
        if not isinstance(n, dict) or not str(n.get("text") or "").strip():
            continue
        # (a note from the old file has no id: one made from its words, so
        # it's the same every time it's read - Forget finds it)
        nid = str(n.get("id") or hashlib.sha1(str(n["text"]).encode("utf-8")).hexdigest()[:8])
        out["notes"].append({"id": nid, "text": str(n["text"])[:200],
                             "source": n.get("source") if n.get("source") in ("you", "ai") else "you",
                             "status": n.get("status") if n.get("status") in ("kept", "pending") else "kept",
                             "at": float(n.get("at") or 0), **({"replaces": n["replaces"]} if n.get("replaces") else {})})
    for k in ("dismissed_habits", "dismissed_undos"):
        out[k] = [str(x)[:300] for x in raw.get(k) or [] if x][-200:]
    # a suggestion nobody answered goes after two weeks
    cut = time.time() - PENDING_DAYS * 86400
    out["notes"] = [n for n in out["notes"] if n["status"] == "kept" or n["at"] >= cut]
    return out


def _save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 2, **d}, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", str(t).lower()) if len(w) > 1 and w not in _STOP}


def _alike(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


# -- reading -----------------------------------------------------------------
def state() -> dict:
    with _lock:
        return _load()


def kept() -> list[dict]:
    return [n for n in state()["notes"] if n["status"] == "kept"]


def kept_texts() -> list[str]:
    return [n["text"] for n in kept()]


def pending() -> list[dict]:
    return [n for n in state()["notes"] if n["status"] == "pending"]


def paused() -> bool:
    return state()["paused"]


# -- learning ------------------------------------------------------------------
def add(text: str, asked: bool, typed: bool = False) -> dict:
    """A note: kept at once when the operator asked for it, else waiting for
    their OK.  {ok, note, status, replaces?} - or {ok: False, error}."""
    text = " ".join(str(text or "").split())[:200]
    if len(text) < 4:
        return {"ok": False, "error": "say what to remember"}
    with _lock:
        d = _load()
        if d["paused"] and not typed:          # (a note the operator types in still goes in)
            return {"ok": False, "error": "learning is paused (Settings -> AI) - nothing new is remembered"}
        if any(n["text"].lower() == text.lower() for n in d["notes"]):
            return {"ok": True, "status": "already", "note": text}
        old = max((n for n in d["notes"] if n["status"] == "kept"), key=lambda n: _alike(n["text"], text), default=None)
        replaces = old if old is not None and _alike(old["text"], text) >= SIMILAR else None
        note = {"id": os.urandom(4).hex(), "text": text, "source": "you" if asked else "ai",
                "status": "kept" if asked else "pending", "at": time.time()}
        if replaces is not None:
            note["replaces"] = replaces["id"]
            if asked:
                d["notes"] = [n for n in d["notes"] if n["id"] != replaces["id"]]
        d["notes"].append(note)
        _trim(d)
        _save(d)
    out = {"ok": True, "status": note["status"], "note": text, "id": note["id"]}
    if replaces is not None:
        out["replaces"] = replaces["text"]
    return out


def _trim(d: dict) -> None:
    """At most MAX_NOTES of each source kept; the operator's own are never
    pushed out by the AI's suggestions."""
    for src in ("you", "ai"):
        mine = [n for n in d["notes"] if n["source"] == src and n["status"] == "kept"]
        for n in mine[:-MAX_NOTES]:
            d["notes"].remove(n)
    pend = [n for n in d["notes"] if n["status"] == "pending"]
    for n in pend[:-10]:
        d["notes"].remove(n)


def decide(nid: str, keep: bool) -> dict:
    """The operator's answer to a suggestion (or to a kept note: keep=False
    forgets it)."""
    with _lock:
        d = _load()
        n = next((x for x in d["notes"] if x["id"] == str(nid)), None)
        if n is None:
            return {"ok": False, "error": "that note is gone"}
        if keep:
            n["status"] = "kept"
            if n.get("replaces"):
                d["notes"] = [x for x in d["notes"] if x["id"] != n["replaces"]]
                n.pop("replaces", None)
        else:
            d["notes"].remove(n)
        _trim(d)
        _save(d)
    return {"ok": True}


def forget_text(number: int) -> dict:
    """Forget the kept note with this number (1 = the first, as the AI sees them)."""
    ks = kept()
    if not 1 <= int(number) <= len(ks):
        return {"ok": False, "error": f"there is no note {number}"}
    return decide(ks[int(number) - 1]["id"], False)


def dismiss_habit(sig: str) -> None:
    with _lock:
        d = _load()
        if sig not in d["dismissed_habits"]:
            d["dismissed_habits"].append(str(sig)[:300])
        _save(d)


def dismissed_habits() -> set[str]:
    return set(state()["dismissed_habits"])


def dismiss_undo(rid: str) -> None:
    with _lock:
        d = _load()
        if rid not in d["dismissed_undos"]:
            d["dismissed_undos"].append(str(rid)[:40])
        _save(d)


def dismissed_undos() -> set[str]:
    return set(state()["dismissed_undos"])


def set_paused(on: bool) -> None:
    with _lock:
        d = _load()
        d["paused"] = bool(on)
        _save(d)


def forget_all() -> None:
    """Start again: every note, suggestion and dismissal gone (kept examples
    and the shows' habits are separate - they live in the examples file and
    the shows)."""
    with _lock:
        _save({"notes": [], "dismissed_habits": [], "dismissed_undos": [], "paused": paused_unlocked()})


def paused_unlocked() -> bool:
    return _load()["paused"]
