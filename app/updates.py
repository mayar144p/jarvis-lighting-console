"""Is a newer Haze out?  (The installed app only - a desk run from the code
brings itself up to date from git when it opens.)

Once a day at most, in the background, the desk asks GitHub for the latest
release of Haze and compares its version with this one; the top bar then
offers it ("Haze 0.3.0 is out - Download").  Nothing is downloaded or
installed by itself, and the check can be turned off (Settings -> Screen).

The app passes its version (HAZE_VERSION) and that it is the installed one
(HAZE_PACKED) when it starts the engine.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

from . import config

# the repository's releases (GitHub sends a renamed repository's old name on)
REPO = "mayar144p/jarvis-lighting-console"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
EVERY_S = 24 * 3600
_lock = threading.Lock()
_running = {"t": None}


def _path():
    return config.DATA / "update_check.json"


def _load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


def this_version() -> str:
    return str(os.environ.get("HAZE_VERSION") or "").strip()


def installed() -> bool:
    return str(os.environ.get("HAZE_PACKED") or "") == "1" and bool(this_version())


def _ver(v) -> tuple:
    nums = re.findall(r"\d+", str(v or ""))[:3]
    return tuple(int(n) for n in nums) + (0,) * (3 - len(nums)) if nums else (0, 0, 0)


def newer(remote: str, local: str) -> bool:
    return _ver(remote) > _ver(local)


def pick_asset(assets: list, platform: str = sys.platform) -> str:
    """The installer for this computer from a release's files ("" if none)."""
    want = (".exe",) if platform.startswith("win") else (".dmg",) if platform == "darwin" else ()
    for a in assets or []:
        name = str((a or {}).get("name") or "").lower()
        if want and name.endswith(want):
            return str(a.get("browser_download_url") or "")
    return ""


def parse(release: dict, local: str) -> dict:
    tag = str(release.get("tag_name") or "")
    out = {"latest": tag.lstrip("vV"), "available": bool(tag) and newer(tag, local) and not release.get("prerelease"),
           "url": pick_asset(release.get("assets") or []) or str(release.get("html_url") or ""),
           "page": str(release.get("html_url") or ""), "notes": str(release.get("body") or "")[:400]}
    return out


def _fetch() -> None:
    d = _load()
    try:
        req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "Haze desk"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            rel = json.loads(resp.read(400_000).decode("utf-8"))
        d.update(parse(rel, this_version()), checked=time.time(), error="")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        d.update(checked=time.time(), error=str(exc)[:120])
    _save({k: v for k, v in d.items()})


def status() -> dict:
    """What the top bar shows (and a check started in the background when
    it's due - it never makes the desk wait)."""
    d = _load()
    if not installed():
        return {"available": False, "installed": False}
    if d.get("off"):
        return {"available": False, "installed": True, "off": True, "version": this_version()}
    if time.time() - float(d.get("checked") or 0) > EVERY_S:
        with _lock:
            t = _running["t"]
            if t is None or not t.is_alive():
                _running["t"] = threading.Thread(target=_fetch, name="haze-update-check", daemon=True)
                _running["t"].start()
    avail = bool(d.get("available")) and newer(d.get("latest") or "", this_version())
    return {"available": avail, "installed": True, "off": False, "version": this_version(),
            "latest": d.get("latest") or "", "url": d.get("url") or "", "page": d.get("page") or "",
            "notes": d.get("notes") or "", "dismissed": d.get("dismissed") == d.get("latest")}


def set_off(off: bool) -> dict:
    d = _load()
    d["off"] = bool(off)
    _save(d)
    return status()


def dismiss() -> dict:
    """"Not now" for this version (the next one is offered again)."""
    d = _load()
    d["dismissed"] = d.get("latest") or ""
    _save(d)
    return status()
