"""Good examples for teaching the offline AI (tools/finetune/).

Every copilot request is written down when it finishes - what was asked,
the conversation the AI had with the desk (its tool calls and what came
back), and its answer - and marked when the operator keeps it (a good
example) or throws it away (a bad one).  `export` turns the good ones into
the training file the fine-tuning kit takes.

Pictures are left out (only "[a picture]"), and nothing from .env is ever in
a conversation.  It stays on this computer: data/ai_examples.jsonl.
"""
from __future__ import annotations

import json
import os
import threading
import time

from . import config

MAX_TOOL_TEXT = 4000
MAX_FILE = 200 * 1024 * 1024
_lock = threading.Lock()


def _path():
    return config.DATA / "ai_examples.jsonl"


def _clean(msg: dict) -> dict:
    m = dict(msg)
    c = m.get("content")
    if isinstance(c, list):                  # a message with a picture: the words only
        m["content"] = " ".join(str(x.get("text") or "") for x in c if isinstance(x, dict) and x.get("type") == "text") \
            + " [a picture]"
    if m.get("role") == "tool" and isinstance(m.get("content"), str):
        m["content"] = m["content"][:MAX_TOOL_TEXT]
    return m


def record(msgs: list[dict], message: str, final: str, steps: list[dict], changed: bool) -> str:
    """Write one finished request; its id comes back (for the verdict)."""
    rid = os.urandom(6).hex()
    row = {"id": rid, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "prompt": message[:2000],
           "messages": [_clean(m) for m in msgs] + ([{"role": "assistant", "content": final}] if final else []),
           "steps": [{"action": s.get("action"), "ok": s.get("ok")} for s in steps][:60], "changed": bool(changed)}
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            if p.exists() and p.stat().st_size > MAX_FILE:
                return ""
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError:
        return ""
    return rid


def verdict(rid: str, good: bool) -> bool:
    if not rid:
        return False
    try:
        with _lock, _path().open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": str(rid)[:20], "verdict": "good" if good else "bad"}) + "\n")
        return True
    except OSError:
        return False


def undone(rid: str) -> bool:
    """The operator pressed Ctrl+Z on what this request did: a thrown-away
    example, and one the AI is told about next time ("not like that")."""
    if not rid:
        return False
    try:
        with _lock, _path().open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": str(rid)[:20], "verdict": "bad", "undone": True}) + "\n")
        return True
    except OSError:
        return False


def recently_undone(k: int = 5) -> list[dict]:
    """The last few requests whose result the operator undid, newest first:
    [{prompt, how}] (for the AI: don't do it that way again)."""
    try:
        st = _path().stat()
        key = (st.st_mtime_ns, st.st_size, k)
    except OSError:
        return []
    if _undone_cache.get("key") == key:
        return _undone_cache["rows"]
    rows, undone_ids = {}, []
    try:
        with _path().open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("undone"):
                    undone_ids.append(r.get("id"))
                elif r.get("id") and "messages" in r:
                    rows[r["id"]] = r
    except OSError:
        return []
    out, seen = [], set()
    for rid in reversed(undone_ids):
        r = rows.get(rid)
        if r and rid not in seen:
            seen.add(rid)
            out.append({"prompt": r.get("prompt", "")[:200], "how": _how(r)[:8]})
        if len(out) >= k:
            break
    _undone_cache.update(key=key, rows=out)
    return out


_undone_cache: dict = {}


def _rows() -> list[dict]:
    rows, verdicts = {}, {}
    undone: set = set()
    try:
        with _path().open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if "verdict" in r and "messages" not in r:
                    verdicts[r["id"]] = r["verdict"]
                    if r.get("undone"):
                        undone.add(r["id"])
                elif r.get("id"):
                    rows[r["id"]] = r
    except OSError:
        return []
    for k, v in verdicts.items():
        if k in rows:
            rows[k]["verdict"] = v
            if k in undone:
                rows[k]["undone"] = True
    return list(rows.values())


def stats() -> dict:
    rows = _rows()
    good = sum(1 for r in rows if r.get("verdict") == "good")
    bad = sum(1 for r in rows if r.get("verdict") == "bad")
    answers = sum(1 for r in rows if not r.get("verdict") and not r.get("changed"))
    return {"all": len(rows), "kept": good, "thrown": bad, "answers": answers, "usable": good + answers,
            "undone": sum(1 for r in rows if r.get("undone"))}


def export(dest=None, tools: list[dict] | None = None) -> dict:
    """The good examples as a training file: kept ones, and answers that
    changed nothing (questions answered) unless marked bad.  One JSON per
    line: {messages, tools} - the chat format the kit trains on."""
    rows = [r for r in _rows() if r.get("verdict") == "good" or (not r.get("verdict") and not r.get("changed"))]
    dest = dest or (config.DATA / "ai_training.jsonl")
    with open(dest, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"messages": r["messages"], **({"tools": tools} if tools else {})}, ensure_ascii=False) + "\n")
    return {"examples": len(rows), "file": str(dest)}


# -- the operator's own kept examples, shown to the AI with a request like them
_STOP = frozenset("a an the and or to of on in at for with me my it this that is are be do make set some all "
                  "please can you i want would like just them they lights light".split())
_index: dict = {"key": None, "rows": []}


def _words(text: str) -> set[str]:
    import re
    return {w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(w) > 1 and w not in _STOP}


def _how(row: dict) -> list[str]:
    """What the AI did in a kept example, short: its tool calls in order."""
    out = []
    for m in row.get("messages") or []:
        for c in m.get("tool_calls") or []:
            fn = c.get("function") or {}
            args = fn.get("arguments") or ""
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            out.append(f"{fn.get('name')} {args[:220]}")
    return out[:14]


def _kept() -> list[dict]:
    """The kept examples (cached until the file changes)."""
    try:
        st = _path().stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    if _index["key"] != key:
        rows = [r for r in _rows() if r.get("verdict") == "good" and r.get("prompt")]
        _index.update(key=key, rows=[{"prompt": r["prompt"], "words": _words(r["prompt"]), "how": _how(r),
                                      "reply": next((str(m.get("content") or "") for m in reversed(r["messages"])
                                                     if m.get("role") == "assistant" and m.get("content")), "")[:240]}
                                     for r in rows[-2000:]])
    return _index["rows"]


def similar(message: str, k: int = 3) -> list[dict]:
    """Up to k kept examples most like this request (by the words they
    share; nothing under a third alike)."""
    want = _words(message)
    if not want:
        return []
    scored = []
    for r in _kept():
        if not r["how"] or not r["words"]:
            continue
        s = len(want & r["words"]) / len(want | r["words"])
        if s >= 0.34:
            scored.append((s, r))
    scored.sort(key=lambda t: -t[0])
    out, seen = [], set()
    for _s, r in scored:
        if r["prompt"].lower() in seen:
            continue
        seen.add(r["prompt"].lower())
        out.append({"prompt": r["prompt"], "how": r["how"], "reply": r["reply"]})
        if len(out) >= k:
            break
    return out


def describe_similar(message: str, k: int = 3) -> str:
    ex = similar(message, k)
    if not ex:
        return ""
    parts = ["REQUESTS LIKE THIS ONE THAT THE OPERATOR KEPT (done the way they liked - follow the approach, "
             "adapted to this rig and this request):"]
    for e in ex:
        parts.append(f'  "{e["prompt"][:200]}"\n    you did: ' + " -> ".join(e["how"])
                     + (f'\n    and said: {e["reply"]}' if e["reply"] else ""))
    return "\n".join(parts)
