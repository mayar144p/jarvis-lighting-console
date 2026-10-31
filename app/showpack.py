"""A show and everything it needs, in one file - to take it to another
computer: Show menu -> Save everything to a file... / Open everything
from a file...

The file (<show>.haze) is a zip:

    manifest.json   what it is, which show, when
    show.json       the saved show (patch, groups, cues, buttons, pages,
                    moves, timeline, venue, DMX output...)
    fixtures.json   the fixture definitions of every model in its patch -
                    imported or made by hand ones too - and what was found
                    out about each model on the real lights (the value that
                    opens a shutter, colour matching, taught gobo slots,
                    "strobe buttons use its own strobe", movement times)
    files/<folder>/<name>
                    the files the show points at: timeline music, floor
                    plans, screen media, gobo pictures

Opening it puts the fixtures and those settings into this computer's
library, copies the files in (never over a different one), saves the show
(under a new name if a different show already has its name) and opens it.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app import config, fixtures

KIND = "haze-pack"
OLD_KINDS = ("jarvis-pack",)                    # files from before the desk was called Haze
VERSION = 1
# the data folders whose files a show (or a fixture) points at by name
FOLDERS = ("audio", "underlays", "screens", "gobos")
MAX_BYTES = 1024 * 1024 * 1024          # a pack, uploaded: 1 GB at most
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


def _models(patch: list[dict]) -> set[tuple[str, str]]:
    return {(str(h.get("manufacturer") or ""), str(h.get("model") or "")) for h in patch}


def _fixture_rows(db_path: Path, models: set[tuple[str, str]]) -> dict:
    """The library rows of these models: fixtures + modes, the per-model
    settings (channel_overrides) and the movement times (motion)."""
    out = {"fixtures": [], "overrides": [], "motion": []}
    with fixtures.db(db_path) as conn:
        fixtures._override_table(conn)
        fixtures._motion_table(conn)
        for man, model in sorted(models):
            for f in conn.execute("SELECT id, manufacturer, model, source FROM fixtures"
                                  " WHERE manufacturer = ? AND model = ?", (man, model)):
                modes = [{"name": m["name"], "channel_count": m["channel_count"],
                          "channels": json.loads(m["channels"] or "[]"),
                          "detail": json.loads(m["detail"] or "[]")}
                         for m in conn.execute("SELECT name, channel_count, channels, detail FROM modes"
                                               " WHERE fixture_id = ? ORDER BY id", (f["id"],))]
                out["fixtures"].append({"manufacturer": f["manufacturer"], "model": f["model"],
                                        "source": f["source"] or "", "modes": modes})
            for r in conn.execute("SELECT mode, role, key, value FROM channel_overrides"
                                  " WHERE manufacturer = ? AND model = ?", (man, model)):
                out["overrides"].append({"manufacturer": man, "model": model, "mode": r["mode"],
                                         "role": r["role"], "key": r["key"], "value": r["value"]})
            m = conn.execute("SELECT pan_s, tilt_s FROM motion WHERE manufacturer = ? AND model = ?",
                             (man, model)).fetchone()
            if m:
                out["motion"].append({"manufacturer": man, "model": model, "pan_s": m["pan_s"], "tilt_s": m["tilt_s"]})
    return out


def _files(text: str) -> list[tuple[str, Path]]:
    """(folder, path) of every data file the show / fixtures text names."""
    out = []
    for folder in FOLDERS:
        d = config.DATA / folder
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.is_file() and _SAFE_NAME.match(p.name) and (p.stem in text or p.name in text):
                out.append((folder, p))
    return out


def build(eng, name: str) -> tuple[str, bytes]:
    """(file name, zip bytes) for the saved show `name`."""
    label = eng._safe_name(name or eng.show_file or "show")
    path = eng.show_dir / f"{label}.json"
    if not path.is_file():
        raise ValueError(f"save the show first (no show {label!r})")
    show_text = path.read_text(encoding="utf-8")
    try:
        patch = json.loads(show_text).get("patch") or []
    except ValueError as exc:
        raise ValueError(f"the show {label!r} is damaged ({exc})") from exc
    lib = _fixture_rows(eng.db_path, _models(patch))
    lib_text = json.dumps(lib)
    files = _files(show_text + lib_text)
    manifest = {"kind": KIND, "version": VERSION, "show": label,
                "saved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "lights": len(patch), "fixtures": len(lib["fixtures"]),
                "settings": len(lib["overrides"]) + len(lib["motion"]), "files": len(files)}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        z.writestr("show.json", show_text)
        z.writestr("fixtures.json", lib_text)
        for folder, p in files:
            z.write(p, f"files/{folder}/{p.name}")
    return f"{label}.haze", buf.getvalue()


def _store_library(db_path: Path, lib: dict) -> int:
    """The pack's fixture definitions and per-model settings into this
    computer's library (the pack's wins: it is what the show was made with)."""
    n = 0
    with fixtures.db(db_path) as conn:
        fixtures._override_table(conn)
        fixtures._motion_table(conn)
        now = datetime.now(timezone.utc).isoformat()
        for f in lib.get("fixtures") or []:
            man, model, source = str(f.get("manufacturer") or ""), str(f.get("model") or ""), str(f.get("source") or "")
            if not model:
                continue
            conn.execute("INSERT OR IGNORE INTO fixtures (manufacturer, model, source, imported_at) VALUES (?,?,?,?)",
                         (man, model, source, now))
            fid = conn.execute("SELECT id FROM fixtures WHERE manufacturer = ? AND model = ? AND source = ?",
                               (man, model, source)).fetchone()["id"]
            for m in f.get("modes") or []:
                if not isinstance(m, dict) or not m.get("name"):
                    continue
                conn.execute("DELETE FROM modes WHERE fixture_id = ? AND name = ?", (fid, m["name"]))
                conn.execute("INSERT INTO modes (fixture_id, name, channel_count, channels, detail) VALUES (?,?,?,?,?)",
                             (fid, str(m["name"]), int(m.get("channel_count") or 0),
                              json.dumps(m.get("channels") or []), json.dumps(m.get("detail") or [])))
            n += 1
        for o in lib.get("overrides") or []:
            if isinstance(o, dict) and o.get("role") and o.get("key"):
                conn.execute("INSERT OR REPLACE INTO channel_overrides VALUES (?,?,?,?,?,?)",
                             (str(o.get("manufacturer") or ""), str(o.get("model") or ""), str(o.get("mode") or ""),
                              str(o["role"]), str(o["key"]), str(o.get("value"))))
        for m in lib.get("motion") or []:
            if isinstance(m, dict):
                conn.execute("INSERT OR REPLACE INTO motion (manufacturer, model, pan_s, tilt_s) VALUES (?,?,?,?)",
                             (str(m.get("manufacturer") or ""), str(m.get("model") or ""), m.get("pan_s"), m.get("tilt_s")))
    fixtures.invalidate_cache()
    return n


def restore(eng, data: bytes) -> dict:
    """Open a pack: library, files and the show in; the show opened."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(z.read("manifest.json"))
        show_text = z.read("show.json").decode("utf-8")
        lib = json.loads(z.read("fixtures.json"))
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"this is not a Haze show file ({exc})") from None
    if manifest.get("kind") != KIND and manifest.get("kind") not in OLD_KINDS:
        raise ValueError("this is not a Haze show file")
    if int(manifest.get("version") or 0) > VERSION:
        raise ValueError("this file was made by a newer Haze - update this one first")
    try:
        json.loads(show_text)
    except ValueError as exc:
        raise ValueError(f"the show in this file is damaged ({exc})") from None
    n_fix = _store_library(eng.db_path, lib)
    # the files the show points at: in, never over a different file
    copied = 0
    for info in z.infolist():
        parts = info.filename.split("/")
        if len(parts) != 3 or parts[0] != "files" or parts[1] not in FOLDERS or not _SAFE_NAME.match(parts[2]):
            continue
        dest = config.DATA / parts[1] / parts[2]
        if dest.exists():
            continue                     # content-named: the same file is already here
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(z.read(info))
        copied += 1
    # the show: its own name, or a new one if a different show has it here
    base = eng._safe_name(str(manifest.get("show") or "show"))
    label, i = base, 2
    while (eng.show_dir / f"{label}.json").is_file() and \
            (eng.show_dir / f"{label}.json").read_text(encoding="utf-8") != show_text:
        label = eng._safe_name(f"{base}-{i}")
        i += 1
    eng.show_dir.mkdir(parents=True, exist_ok=True)
    (eng.show_dir / f"{label}.json").write_text(show_text, encoding="utf-8")
    drop = getattr(eng, "_drop_fixture_caches", None)
    if drop:
        drop()
    r = eng.act("load_show", name=label)
    if not r.get("ok"):
        raise ValueError(r.get("error") or "the show could not be opened")
    renamed = label != base
    return {"show": label, "renamed": renamed, "fixtures": n_fix, "files": copied,
            "summary": f"opened {label!r}" + (f" (a different show here was already called {base!r})" if renamed else "")
                       + f" - {n_fix} fixture model(s) and {copied} file(s) brought in"}
