"""A bug report with the evidence attached ("bug bundle", backlog A11).

The desk gathers what a fix needs - the light's fixture file and how the
desk read it, its live DMX, what the 3D thinks it does, a picture of the
3D, the desk version and recent errors, and (ticked by default) the whole
show - into one zip in DATA/bug_reports/, and builds the link to a
pre-filled GitHub issue (the forms in .github/ISSUE_TEMPLATE/).  The
reporter sees the list of files before anything is saved, and drags the
zip into the issue.

Never in it: `.env` or any file from disk other than the light's own
fixture file; and every secret the desk holds (the AI key, the desk's
token, the GDTF Share login) is blanked out of every file, along with
anything shaped like an API key.
"""
from __future__ import annotations

import collections
import json
import os
import platform
import re
import sys
import time
import urllib.parse
import zipfile
from pathlib import Path

from app import config

REPO = "mayar144p/jarvis-lighting-console"
# the server's last errors (main.py records them), newest last
SERVER_ERRORS: collections.deque = collections.deque(maxlen=30)
_KEYLIKE = re.compile(r"\b(AIza[0-9A-Za-z_\-]{20,}|sk-[0-9A-Za-z_\-]{20,}|ghp_[0-9A-Za-z]{20,}|"
                      r"github_pat_[0-9A-Za-z_]{20,}|xox[abp]-[0-9A-Za-z\-]{10,})\b")
AREAS = {   # the desk's ticks -> the light form's choices
    "dmx": "The real light (DMX)", "3d": "The 3D view",
    "programmer": "The programmer (wrong or missing controls)",
    "effects": "Effects / lasers / fog / confetti", "cues": "Cues or playbacks",
    "crash": "The desk crashed or froze",
}


def note_error(where: str, exc: BaseException | str) -> None:
    SERVER_ERRORS.append(f"{time.strftime('%H:%M:%S')} {where}: {exc}"[:400])


def _secrets() -> list[str]:
    from app import llm
    vals = [config.LLM_API_KEY, llm.settings()["key"], config.CONSOLE_TOKEN,
            config.GDTF_SHARE_PASSWORD, config.GDTF_SHARE_USER]
    return sorted({str(v) for v in vals if v and len(str(v)) >= 4}, key=len, reverse=True)


def scrub(text: str) -> str:
    """Every secret the desk holds, and anything shaped like a key, blanked."""
    for s in _secrets():
        text = text.replace(s, "[removed]")
    return _KEYLIKE.sub("[removed]", text)


_VERSION: str | None = None


def desk_version() -> str:
    """The commit the desk runs, read from .git's own files - no git
    program: on Windows a slow git under a timeout could leave the report
    waiting forever (its helper process keeps the pipe open)."""
    global _VERSION
    if _VERSION is None:
        _VERSION = _read_version(Path(config.ROOT))
    return _VERSION


def _read_version(root: Path) -> str:
    try:
        git = root / ".git"
        if git.is_file():                              # a worktree: "gitdir: <path>"
            git = (root / git.read_text(encoding="utf-8").split(":", 1)[1].strip()).resolve()
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:7]                            # a detached checkout
        ref = head.split(":", 1)[1].strip()
        p = git / ref
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()[:7] + " " + ref.rsplit("/", 1)[-1]
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line[:7] + " " + ref.rsplit("/", 1)[-1]
    except (OSError, IndexError, ValueError):
        pass
    return "unknown"


def computer() -> str:
    app = "desktop app" if os.environ.get("JARVIS_DESKTOP") else "browser"
    return f"{platform.system()} {platform.release()}, Python {sys.version.split()[0]}, {app}"


def _fixture_file(eng, head: dict) -> tuple[str, bytes] | None:
    """The light's own fixture file (library, GDTF Share or inbox), if found."""
    from app import fixlib
    src = eng.model_source(head)
    if ":" in src and src.split(":", 1)[0] in ("qlc", "ofl"):
        kind, key = src.split(":", 1)
        path = fixlib._bundle(kind)
        if path:
            try:
                with zipfile.ZipFile(path) as zf:
                    return Path(key).name, zf.read("fixtures/" + key)
            except (KeyError, OSError, zipfile.BadZipFile):
                return None
    if src.lower().endswith(".gdtf"):
        for folder in (config.GDTF_SHARE_CACHE, config.INBOX):
            p = Path(folder) / src
            if p.is_file():
                return p.name, p.read_bytes()
    return None


def gather(eng, head_no: int | None, what: str, areas: list[str], include_show: bool,
           picture: bytes = b"", page_errors: list[str] | None = None) -> dict[str, bytes]:
    """{name in the zip: bytes} - everything the report would carry."""
    files: dict[str, bytes] = {}
    head = next((h for h in eng.patch if h["head_no"] == head_no), None) if head_no else None
    info = {"what": what, "areas": areas, "desk": desk_version(), "computer": computer(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), "output": dict(eng.dmx_target),
            "lights_patched": len(eng.patch)}
    if head:
        info["light"] = {k: head.get(k) for k in ("head_no", "name", "manufacturer", "model", "mode",
                                                  "universe", "address", "channels", "map")}
        info["light"]["fixture_file"] = eng.model_source(head)
        files["light/fixture-as-the-desk-read-it.json"] = json.dumps(
            eng._fixture_db(head.get("manufacturer"), head.get("model")) or {}, indent=1, default=str).encode()
        own = _fixture_file(eng, head)
        if own:
            files["light/" + own[0]] = own[1]
        files["light/dmx-channels.json"] = json.dumps(eng.channel_report([head_no]), indent=1, default=str).encode()
        row = next((r for r in eng._looks() if r.get("n") == head_no), {})
        files["light/what-the-3d-is-told.json"] = json.dumps(row, indent=1, default=str).encode()
    files["report.json"] = json.dumps(info, indent=1, default=str).encode()
    if picture:
        files["3d-view.png"] = picture
    errors = list(SERVER_ERRORS) + [f"page: {e}" for e in (page_errors or [])[-30:]]
    files["recent-errors.txt"] = ("\n".join(errors) or "none").encode()
    if include_show:
        with eng.lock:
            files["show.json"] = eng._autosave_payload().encode()
    # every text file: no secret survives
    return {n: scrub(b.decode("utf-8")).encode() if n.endswith((".json", ".txt")) else b
            for n, b in files.items()}


def save(files: dict[str, bytes], head: dict | None) -> Path:
    folder = config.DATA / "bug_reports"
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", f"{head.get('manufacturer', '')} {head.get('model', '')}".lower()).strip("-") \
        if head else "desk"
    path = folder / f"jarvis-report-{time.strftime('%Y%m%d-%H%M%S')}-{slug[:40] or 'light'}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def issue_url(head: dict | None, what: str, areas: list[str], zip_name: str) -> str:
    """A new GitHub issue on the right form, filled in (the zip is dragged in)."""
    first = (what.strip().splitlines() or ["(no description)"])[0][:80]
    files = f"Drag in **{zip_name}** (it was saved for you; it never contains `.env` or keys)."
    version = f"{desk_version()} · {computer()}"
    if head:
        maker, model = head.get("manufacturer") or "", head.get("model") or ""
        q = {"template": "light-bug.yml", "title": f"[Light] {maker} {model}: {first}".strip(),
             "labels": ",".join(["light-bug"] + [f"{k}:{v}" for k, v in (("brand", maker), ("model", model)) if v]),
             "light": f"{maker} {model}, {head.get('mode') or '?'} mode".strip(),
             "where": f"#{head.get('head_no')} at {head.get('universe')}.{int(head.get('address') or 0):03d}",
             "area": ",".join(AREAS[a] for a in areas if a in AREAS) or AREAS["dmx"],
             "what": what, "files": files, "version": version}
    else:
        q = {"template": "bug.yml", "title": f"[Bug] {first}", "labels": "bug",
             "area": "Something else", "what": what, "files": files, "version": version}
    return f"https://github.com/{REPO}/issues/new?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def contents(files: dict[str, bytes]) -> list[dict]:
    return [{"name": n, "bytes": len(b)} for n, b in files.items()]


def report(eng, head_no: int | None, what: str, areas, include_show: bool, picture: bytes,
           page_errors, preview: bool = False) -> dict:
    """Preview (what would go in) or save it and give the issue link."""
    areas = [a for a in (areas or []) if a in AREAS]
    head = next((dict(h) for h in eng.patch if h["head_no"] == head_no), None) if head_no else None
    files = gather(eng, head_no if head else None, str(what or "")[:4000], areas,
                   bool(include_show), picture, page_errors)
    if preview:
        return {"files": contents(files)}
    path = save(files, head)
    return {"files": contents(files), "zip": path.name, "url": issue_url(head, str(what or ""), areas, path.name)}


def read_saved(name: str) -> bytes | None:
    """A saved report's zip by its name (only from DATA/bug_reports)."""
    if not re.fullmatch(r"jarvis-report-[\w\-]{1,120}\.zip", name or ""):
        return None
    p = config.DATA / "bug_reports" / name
    return p.read_bytes() if p.is_file() else None

