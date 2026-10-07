"""The desk's own offline AI (backlog A12): llama.cpp's server, shipped
inside the desktop app, running one model file kept in DATA/ai/.

- Nothing to install: the runtime is in the app (desktop/llama/, or
  LLAMA_SERVER, or llama-server on the PATH); the model is one .gguf file.
- Started in the background the first time the copilot needs it, stopped
  after IDLE_S without a request (the graphics card goes back to the 3D
  view) and when the desk closes.
- The model: downloaded in the desk (pause / resume, checked against the
  file's SHA-256 when done; never while the output is live), or an "AI
  pack" - a .gguf copied from a USB stick - imported; Remove frees the
  space.  It lives in the data folder, so app updates never touch it.
- `suggest()` picks the model from the computer: memory and free disk.

The network and the runtime are injectable, so the selftests run all of
it with a fake Hugging Face and a fake llama-server.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from app import config

HF = "https://huggingface.co"
# What the desk offers.  The file itself is found in the repo's listing
# when downloading (its name, size and SHA-256 come from there).
CATALOG = [
    {"id": "qwen3-8b", "label": "Qwen3 8B", "repo": "Qwen/Qwen3-8B-GGUF", "quant": "Q4_K_M",
     "gb": 5.0, "ram_gb": 16, "note": "for 16 GB computers"},
    {"id": "qwen3-14b", "label": "Qwen3 14B", "repo": "Qwen/Qwen3-14B-GGUF", "quant": "Q4_K_M",
     "gb": 9.0, "ram_gb": 32, "note": "smarter; 32 GB or a 12 GB graphics card"},
]
IDLE_S = 600
CHUNK = 1 << 20

_lock = threading.RLock()
_proc: subprocess.Popen | None = None
_port = 0
_last_use = 0.0
_dl: dict = {}                       # the download in progress (or the last one)
transport = None                     # tests: fn(url, headers) -> (status, headers, iterable of bytes)


def folder() -> Path:
    p = config.DATA / "ai"
    p.mkdir(parents=True, exist_ok=True)
    return p


def models() -> list[dict]:
    return [{"file": p.name, "bytes": p.stat().st_size} for p in sorted(folder().glob("*.gguf"))]


def runtime() -> list[str] | None:
    """The command that runs llama.cpp's server, or None."""
    env = os.environ.get("LLAMA_SERVER", "")
    if env:
        return [sys.executable, env] if env.endswith(".py") else [env]
    exe = "llama-server.exe" if os.name == "nt" else "llama-server"
    for base in (os.environ.get("CONSOLE_LLAMA_DIR", ""), str(config.ROOT / "desktop" / "llama")):
        if base:
            hit = next(iter(sorted(Path(base).rglob(exe))), None) if Path(base).is_dir() else None
            if hit:
                return [str(hit)]
    found = shutil.which("llama-server")
    return [found] if found else None


def ready() -> bool:
    """Can the desk start its own AI (a runtime and a model)?"""
    return runtime() is not None and bool(models())


def url() -> str | None:
    with _lock:
        if _proc is not None and _proc.poll() is None and _port:
            touch()
            return f"http://127.0.0.1:{_port}/v1"
    return None


def touch() -> None:
    global _last_use
    _last_use = time.monotonic()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _model_file() -> Path | None:
    from app import llm
    want = llm.settings().get("local_model") or ""
    files = [folder() / m["file"] for m in models()]
    pick = next((f for f in files if f.name == want or f.stem == want), None)
    return pick or (max(files, key=lambda f: f.stat().st_size) if files else None)


def start(wait_s: float = 180.0) -> bool:
    """Start the desk's AI if it can be (a runtime and a model); True once it
    answers.  The first start loads the model: it can take a minute."""
    global _proc, _port
    with _lock:
        if url():
            return True
        cmd, model = runtime(), _model_file()
        if not cmd or not model:
            return False
        _port = _free_port()
        args = cmd + ["-m", str(model), "--host", "127.0.0.1", "--port", str(_port),
                      "-c", "8192", "-ngl", "99", "--jinja", "--alias", model.stem]
        flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
        _proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=flags)
        touch()
    t_end = time.monotonic() + wait_s
    while time.monotonic() < t_end:
        if _proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{_port}/v1/models", timeout=2) as r:
                if r.status == 200:
                    _watch()
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    stop()
    return False


def stop() -> None:
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _proc.kill()
        _proc = None


atexit.register(stop)
_watching = False


def _watch() -> None:
    """Stop the AI after IDLE_S without a request (frees the graphics card)."""
    global _watching
    if _watching:
        return
    _watching = True

    def loop():
        global _watching
        while True:
            time.sleep(15)
            with _lock:
                if _proc is None or _proc.poll() is not None:
                    _watching = False
                    return
                if time.monotonic() - _last_use > IDLE_S:
                    stop()
                    _watching = False
                    return
    threading.Thread(target=loop, daemon=True, name="localai-idle").start()


# ------------------------------------------------------------------ the computer
def memory_gb() -> float:
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + \
                    [(n, ctypes.c_ulonglong) for n in ("total", "avail", "tpf", "apf", "tv", "av", "aev")]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 2 ** 30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except (AttributeError, OSError, ValueError):
        return 0.0


def suggest() -> dict:
    """The model for this computer, and whether it fits."""
    ram = memory_gb()
    free = shutil.disk_usage(folder()).free / 2 ** 30
    pick = CATALOG[1] if ram >= CATALOG[1]["ram_gb"] and free >= CATALOG[1]["gb"] + 2 else CATALOG[0]
    ok = ram >= 12 and free >= pick["gb"] + 1
    why = "" if ok else ("this computer has under 12 GB of memory" if ram < 12
                         else f"needs {pick['gb'] + 1:.0f} GB free on the disk (has {free:.0f} GB)")
    return {"id": pick["id"], "ok": ok, "why": why, "ram_gb": round(ram, 1), "free_gb": round(free, 1)}


# ------------------------------------------------------------------ download
def _get(url: str, headers: dict | None = None):
    if transport:
        return transport(url, headers or {})
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-console/1.0", **(headers or {})})
    resp = urllib.request.urlopen(req, timeout=30)
    return resp.status, dict(resp.headers), iter(lambda: resp.read(CHUNK), b"")


def resolve(item: dict) -> dict:
    """{file, bytes, sha256, url} of the catalog item's .gguf, from the repo's listing."""
    status, _h, body = _get(f"{HF}/api/models/{item['repo']}/tree/main")
    if status != 200:
        raise ValueError(f"Hugging Face answered {status}")
    files = json.loads(b"".join(body).decode("utf-8"))
    hit = next((f for f in files if str(f.get("path", "")).endswith(".gguf")
                and item["quant"].lower() in f["path"].lower()), None)
    if not hit:
        raise ValueError(f"no {item['quant']} file in {item['repo']}")
    lfs = hit.get("lfs") or {}
    return {"file": Path(hit["path"]).name, "bytes": int(lfs.get("size") or hit.get("size") or 0),
            "sha256": str(lfs.get("oid") or lfs.get("sha256") or ""),
            "url": f"{HF}/{item['repo']}/resolve/main/{hit['path']}"}


def download(model_id: str, live: bool = False, background: bool = True) -> dict:
    """Start (or resume) downloading a catalog model into DATA/ai/."""
    if live:
        raise ValueError("not while the output is live - downloads wait for the show to end")
    item = next((m for m in CATALOG if m["id"] == model_id), None)
    if item is None:
        raise ValueError(f"no model {model_id!r}")
    with _lock:
        if _dl.get("running"):
            return status()
        _dl.clear()
        _dl.update(id=model_id, running=True, paused=False, done=0, total=0, error="", file="")
    if background:
        threading.Thread(target=_fetch, args=(item,), daemon=True, name="localai-download").start()
    else:
        _fetch(item)
    return status()


def pause() -> dict:
    _dl["paused"] = True
    return status()


def _fetch(item: dict) -> None:
    try:
        info = resolve(item)
        _dl.update(file=info["file"], total=info["bytes"])
        target = folder() / info["file"]
        part = target.with_name(target.name + ".part")
        have = part.stat().st_size if part.exists() else 0
        sha = hashlib.sha256()
        if have:
            with part.open("rb") as f:                      # resume: hash what's there
                for chunk in iter(lambda: f.read(CHUNK), b""):
                    sha.update(chunk)
        status_, _h, body = _get(info["url"], {"Range": f"bytes={have}-"} if have else {})
        if status_ == 200 and have:                         # no resume on the server: start over
            have, sha = 0, hashlib.sha256()
            part.unlink()
        elif status_ not in (200, 206):
            raise ValueError(f"the download answered {status_}")
        _dl["done"] = have
        with part.open("ab") as out:
            for chunk in body:
                if _dl.get("paused"):
                    _dl.update(running=False)
                    return
                out.write(chunk)
                sha.update(chunk)
                _dl["done"] += len(chunk)
        if info["sha256"] and sha.hexdigest() != info["sha256"]:
            part.unlink()
            raise ValueError("the file arrived damaged (its fingerprint doesn't match) - download it again")
        part.replace(target)
        _dl.update(running=False, finished=True)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        _dl.update(running=False, error=str(exc))


def import_pack(path: str) -> dict:
    """An "AI pack": a .gguf model file (from a USB stick), copied in."""
    src = Path(str(path or "").strip().strip('"'))
    if src.suffix.lower() != ".gguf" or not src.is_file():
        raise ValueError("pick the AI pack: a .gguf model file")
    with src.open("rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError("that file is not an AI model (.gguf)")
    shutil.copyfile(src, folder() / src.name)
    return status()


def remove(file: str) -> dict:
    p = folder() / Path(str(file)).name
    if p.suffix != ".gguf" or not p.is_file():
        raise ValueError("no such model")
    stop()
    p.unlink()
    return status()


def status() -> dict:
    with _lock:
        running = _proc is not None and _proc.poll() is None
    dl = {k: _dl.get(k) for k in ("id", "file", "running", "paused", "done", "total", "error", "finished") if k in _dl}
    return {"runtime": runtime() is not None, "models": models(), "running": running,
            "download": dl, "suggest": suggest(), "catalog": CATALOG}
