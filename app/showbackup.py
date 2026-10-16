"""Shows backed up off the computer: a dead laptop at a gig must not take
the show with it.

Every save copies the show (and its kept versions) into a folder the
operator picks - a OneDrive / Dropbox / Google Drive folder (the cloud app
syncs it), a USB stick, a network share - under "Jarvis shows".  Restore
brings one back.  The copy runs on a thread of its own: a slow stick or a
sleeping network drive never holds up the desk.  The choice is kept in
DATA/backup.json, not in a show.
"""
from __future__ import annotations

import json
import os
import shutil
import string
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SUBDIR = "Jarvis shows"


class Backup:
    """The backup folder and the last copy's outcome."""

    def __init__(self, cfg_path: Path) -> None:
        self.cfg_path = Path(cfg_path)
        self._lock = threading.Lock()
        self.folder = ""
        self.last = None            # when the last copy finished (ISO)
        self.last_error = None
        self.busy = False
        try:
            raw = json.loads(self.cfg_path.read_text(encoding="utf-8"))
            self.folder = str(raw.get("folder") or "")
            self.last = raw.get("last")
        except (OSError, ValueError, AttributeError):
            pass

    # -- settings ----------------------------------------------------------
    def set_folder(self, folder: str) -> dict:
        folder = str(folder or "").strip()
        if folder:
            p = Path(folder).expanduser()
            if not p.is_absolute():
                raise ValueError("pick a whole folder path, e.g. C:\\Users\\you\\OneDrive or E:\\")
            if not p.is_dir():
                raise ValueError(f"{p} isn't a folder on this computer (is the stick plugged in?)")
            try:                                   # can we write there?
                probe = p / ".jarvis-write-test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
            except OSError as exc:
                raise ValueError(f"can't write to {p}: {exc.strerror or exc}") from None
            folder = str(p)
        with self._lock:
            self.folder = folder
            self.last_error = None
        self._save_cfg()
        return self.status()

    def status(self) -> dict:
        with self._lock:
            return {"folder": self.folder, "on": bool(self.folder), "last": self.last,
                    "last_error": self.last_error, "busy": self.busy,
                    "where": str(Path(self.folder) / SUBDIR) if self.folder else ""}

    def _save_cfg(self) -> None:
        try:
            self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
            self.cfg_path.write_text(json.dumps({"folder": self.folder, "last": self.last}), encoding="utf-8")
        except OSError:
            pass

    # -- copying -----------------------------------------------------------
    def copy_later(self, show_dir: Path, label: str) -> bool:
        """After a save: copy this show in the background (False: backup off)."""
        if not self.folder:
            return False
        threading.Thread(target=self.copy_now, args=(Path(show_dir), label),
                         name="jarvis-backup", daemon=True).start()
        return True

    def copy_now(self, show_dir: Path, label: str | None = None) -> dict:
        """Copy one show (or every show) and their versions.  Returns
        {copied, error}; the outcome is also kept for the Settings page."""
        with self._lock:
            folder = self.folder
            self.busy = True
        copied, error = 0, None
        try:
            if not folder:
                raise ValueError("no backup folder chosen (Settings -> Backup)")
            dest = Path(folder) / SUBDIR
            dest.mkdir(parents=True, exist_ok=True)
            names = [label] if label else sorted(p.stem for p in Path(show_dir).glob("*.json")
                                                 if not p.name.startswith("."))
            for name in names:
                src = Path(show_dir) / f"{name}.json"
                if not src.is_file():
                    continue
                _copy_atomic(src, dest / src.name)
                copied += 1
                vsrc = Path(show_dir) / "versions" / name
                if vsrc.is_dir():
                    vdst = dest / "versions" / name
                    vdst.mkdir(parents=True, exist_ok=True)
                    for v in vsrc.glob("*.json"):
                        target = vdst / v.name
                        if not target.exists():        # versions never change once kept
                            _copy_atomic(v, target)
        except (OSError, ValueError) as exc:
            error = str(getattr(exc, "strerror", None) or exc)
        with self._lock:
            self.busy = False
            if error:
                self.last_error = error
            else:
                self.last_error = None
                self.last = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._save_cfg()
        return {"copied": copied, "error": error}

    def shows(self) -> list[dict]:
        """The shows in the backup folder, newest first."""
        dest = Path(self.folder) / SUBDIR if self.folder else None
        if not dest or not dest.is_dir():
            return []
        rows = []
        for p in dest.glob("*.json"):
            if p.name.startswith("."):
                continue
            try:
                saved = json.loads(p.read_text(encoding="utf-8")).get("saved")
            except (OSError, ValueError, AttributeError):
                saved = None
            st = p.stat()
            rows.append({"name": p.stem, "saved": saved, "mtime": st.st_mtime, "bytes": st.st_size,
                         "versions": len(list((dest / "versions" / p.stem).glob("*.json")))
                         if (dest / "versions" / p.stem).is_dir() else 0})
        return sorted(rows, key=lambda r: -r["mtime"])

    def fetch(self, name: str, show_dir: Path) -> Path:
        """Copy one show (and its versions) from the backup into the shows
        folder; the caller keeps the current file as a version first."""
        src = Path(self.folder) / SUBDIR / f"{name}.json"
        if not self.folder or not src.is_file():
            raise ValueError(f"no {name!r} in the backup folder")
        json.loads(src.read_text(encoding="utf-8"))          # a readable show, or refuse
        vsrc = Path(self.folder) / SUBDIR / "versions" / name
        if vsrc.is_dir():
            vdst = Path(show_dir) / "versions" / name
            vdst.mkdir(parents=True, exist_ok=True)
            for v in vsrc.glob("*.json"):
                if not (vdst / v.name).exists():
                    shutil.copy2(v, vdst / v.name)
        return src


def _copy_atomic(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def places() -> list[dict]:
    """Folders worth offering: cloud folders that sync themselves, and
    plugged-in USB sticks / drives.  {label, path}."""
    home = Path.home()
    out = []
    seen = set()

    def add(label, p):
        p = Path(p)
        if p.is_dir() and str(p) not in seen:
            seen.add(str(p))
            out.append({"label": label, "path": str(p)})

    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(env):
            add("OneDrive", os.environ[env])
    for p in sorted(home.glob("OneDrive*")):
        add("OneDrive", p)
    add("Dropbox", home / "Dropbox")
    for p in sorted(home.glob("Google Drive*")):
        add("Google Drive", p)
    cloud = home / "Library" / "CloudStorage"                    # macOS: OneDrive, Dropbox, Google Drive
    if cloud.is_dir():
        for p in sorted(cloud.iterdir()):
            add(p.name.split("-")[0].replace("GoogleDrive", "Google Drive"), p)
    add("iCloud Drive", home / "Library" / "Mobile Documents" / "com~apple~CloudDocs")
    for label, p in _removable():
        add(label, p)
    return out


def _removable() -> list[tuple[str, str]]:
    """USB sticks and other removable drives that are plugged in now."""
    found = []
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            mask = k32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if mask & (1 << i):
                    root = f"{letter}:\\"
                    if k32.GetDriveTypeW(root) == 2:                 # DRIVE_REMOVABLE
                        found.append((f"USB drive {letter}:", root))
        except (OSError, AttributeError):
            pass
    elif sys.platform == "darwin":
        for p in sorted(Path("/Volumes").glob("*")):
            if p.is_dir() and not p.is_symlink() and p.name != "Macintosh HD":
                found.append((f"Drive {p.name}", str(p)))
    else:
        user = os.environ.get("USER", "")
        for base in (Path("/media") / user, Path("/run/media") / user):
            for p in sorted(base.glob("*")) if base.is_dir() else []:
                found.append((f"Drive {p.name}", str(p)))
    return found


def age_text(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        t = datetime.fromisoformat(iso)
        mins = int((datetime.now(timezone.utc) - t).total_seconds() // 60)
    except ValueError:
        return iso
    return "just now" if mins < 1 else f"{mins} min ago" if mins < 60 else \
        f"{mins // 60} h ago" if mins < 48 * 60 else time.strftime("%Y-%m-%d", time.localtime(t.timestamp()))
