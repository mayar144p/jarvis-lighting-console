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


def _rows() -> list[dict]:
    rows, verdicts = {}, {}
    try:
        with _path().open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if "verdict" in r and "messages" not in r:
                    verdicts[r["id"]] = r["verdict"]
                elif r.get("id"):
                    rows[r["id"]] = r
    except OSError:
        return []
    for k, v in verdicts.items():
        if k in rows:
            rows[k]["verdict"] = v
    return list(rows.values())


def stats() -> dict:
    rows = _rows()
    good = sum(1 for r in rows if r.get("verdict") == "good")
    bad = sum(1 for r in rows if r.get("verdict") == "bad")
    answers = sum(1 for r in rows if not r.get("verdict") and not r.get("changed"))
    return {"all": len(rows), "kept": good, "thrown": bad, "answers": answers, "usable": good + answers}


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
