"""Requests from the DJ or the host, from a phone (web/requests.html).

They ask ("more energy", "speech in 2 minutes", "blackout for the intro");
the operator sees it on the desk and taps Do it (the copilot does it, live,
within the room's rules) or No thanks.  The phone sees the answer.

The page has its own code, which can only send requests - never run the
desk - so it can be handed to a DJ without handing over the lights.
"""
from __future__ import annotations

import hmac
import secrets
import threading
import time

KEEP = 30
_lock = threading.Lock()


def _st(eng) -> dict:
    st = eng.__dict__.get("dj_requests")
    if st is None:
        st = eng.dj_requests = {"code": secrets.token_hex(4).upper(), "items": [], "seq": 0}
    return st


def code(eng) -> str:
    return _st(eng)["code"]


def new_code(eng) -> str:
    """A new code: phones with the old one can't send any more."""
    st = _st(eng)
    st["code"] = secrets.token_hex(4).upper()
    return st["code"]


def allowed(eng, given: str) -> bool:
    return bool(given) and hmac.compare_digest(str(given).strip().upper(), code(eng))


def add(eng, text: str, who: str = "", client: str = "") -> dict:
    text = " ".join(str(text or "").split())[:200]
    if not text:
        raise ValueError("say what you'd like")
    st = _st(eng)
    with _lock:
        mine = [r for r in st["items"] if r["client"] == client and r["state"] == "waiting"]
        if len(mine) >= 3:
            raise ValueError("three requests are already waiting - give the operator a moment")
        st["seq"] += 1
        item = {"id": st["seq"], "text": text, "who": str(who or "DJ").strip()[:24] or "DJ",
                "client": str(client or "")[:40], "state": "waiting", "at": time.time(), "note": ""}
        st["items"] = (st["items"] + [item])[-KEEP:]
    return item


def answer(eng, rid: int, yes: bool, note: str = "") -> dict:
    st = _st(eng)
    with _lock:
        item = next((r for r in st["items"] if r["id"] == int(rid)), None)
        if item is None:
            raise ValueError("that request is gone")
        item["state"] = "done" if yes else "declined"
        item["note"] = str(note or "")[:200]
    return item


def pending(eng) -> list[dict]:
    st = eng.__dict__.get("dj_requests")
    if not st:
        return []
    return [{k: r[k] for k in ("id", "text", "who", "at")} for r in st["items"] if r["state"] == "waiting"]


def mine(eng, client: str) -> list[dict]:
    st = _st(eng)
    return [{k: r[k] for k in ("id", "text", "state", "note", "at")} for r in st["items"] if r["client"] == client][-8:][::-1]
