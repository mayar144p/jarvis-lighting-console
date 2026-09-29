"""Rebuild the bundled fixture libraries in app/fixlib/.

    python tools/build_fixture_libraries.py              # fetch both, rebuild
    python tools/build_fixture_libraries.py --ofl DIR --qlc DIR   # from clones

Fetches the Open Fixture Library and the QLC+ fixture definitions with a
shallow, sparse git clone (only their fixture folders), checks that every
fixture parses with app/fixlib.py, and writes one compressed zip per
library holding the original files, a search index and where they came
from.  Fixtures that do not parse are left out and listed.

This is a maintainer tool: the zips are committed, so the desk itself
never needs the network to search or install a library fixture.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import fixlib  # noqa: E402

REPOS = {
    "ofl": ("https://github.com/OpenLightingProject/open-fixture-library", "fixtures"),
    "qlc": ("https://github.com/mcallegari/qlcplus", "resources/fixtures"),
}


def _clone(src: str, into: Path) -> tuple[Path, str]:
    url, sub = REPOS[src]
    dest = into / src
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none",
                    "--sparse", url, str(dest)], check=True)
    subprocess.run(["git", "sparse-checkout", "set", sub], cwd=dest, check=True)
    return dest / sub, _commit(dest)


def _commit(repo: Path) -> str:
    try:
        return subprocess.run(["git", "log", "-1", "--format=%H %cI"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _entry(key: str, parsed: list[dict], manufacturer: str | None = None) -> dict:
    item = parsed[0]
    return {"key": key, "manufacturer": manufacturer or item["manufacturer"],
            "model": item["model"], "type": item.get("type", ""),
            "modes": [[m["name"], m["channel_count"]] for m in item["modes"]]}


def build_ofl(folder: Path, commit: str, out: Path) -> dict:
    makers = json.loads((folder / "manufacturers.json").read_text(encoding="utf-8"))
    index, skipped = [], []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(folder.glob("*/*.json")):
            key = f"{path.parent.name}/{path.name}"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("redirectTo"):
                    continue                # a renamed fixture's stub
                maker = (makers.get(path.parent.name) or {}).get("name") or path.parent.name
                parsed = fixlib.parse_ofl(data, maker, key)
            except Exception as exc:        # noqa: BLE001 - report and skip
                skipped.append(f"{key}: {exc}")
                continue
            zf.writestr("fixtures/" + key, json.dumps(data, separators=(",", ":")))
            index.append(_entry(key, parsed, maker))
        zf.writestr("index.json", json.dumps(index, separators=(",", ":")))
        zf.writestr("meta.json", json.dumps(_meta("ofl", commit, len(index))))
    return {"fixtures": len(index), "skipped": skipped}


def build_qlc(folder: Path, commit: str, out: Path) -> dict:
    index, skipped = [], []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(folder.glob("*/*.qxf")):
            key = f"{path.parent.name}/{path.name}"
            try:
                raw = path.read_bytes()
                parsed = fixlib.parse_qxf(raw)
            except Exception as exc:        # noqa: BLE001 - report and skip
                skipped.append(f"{key}: {exc}")
                continue
            zf.writestr("fixtures/" + key, raw)
            index.append(_entry(key, parsed))
        zf.writestr("index.json", json.dumps(index, separators=(",", ":")))
        zf.writestr("meta.json", json.dumps(_meta("qlc", commit, len(index))))
    return {"fixtures": len(index), "skipped": skipped}


def _meta(src: str, commit: str, count: int) -> dict:
    return {"source": fixlib.SOURCES[src]["name"], "repository": REPOS[src][0],
            "commit": commit, "fixtures": count, "licence": fixlib.SOURCES[src]["licence"],
            "built": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ofl", type=Path, help="an OFL checkout's fixtures/ folder")
    ap.add_argument("--qlc", type=Path, help="a QLC+ checkout's resources/fixtures/ folder")
    args = ap.parse_args()
    fixlib.BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for src, given, build in (("ofl", args.ofl, build_ofl), ("qlc", args.qlc, build_qlc)):
            if given:
                folder, commit = given, _commit(given)
            else:
                print(f"* fetching {fixlib.SOURCES[src]['name']}...")
                folder, commit = _clone(src, tmp)
            out = fixlib.BUNDLE_DIR / fixlib.SOURCES[src]["file"]
            done = build(folder, commit, out)
            size = out.stat().st_size / 1e6
            print(f"* {fixlib.SOURCES[src]['name']}: {done['fixtures']} fixtures, "
                  f"{size:.1f} MB -> {out.relative_to(ROOT)}")
            for line in done["skipped"][:20]:
                print(f"    skipped {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
