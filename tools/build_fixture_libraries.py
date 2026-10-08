"""Rebuild the bundled fixture libraries in app/fixlib/.

    python tools/build_fixture_libraries.py              # fetch both, rebuild
    python tools/build_fixture_libraries.py --ofl DIR --qlc DIR   # from clones
    python tools/build_fixture_libraries.py --gobos-only --ofl DIR --qlc DIR

The gobo pictures the fixtures name (QLC+ resources/gobos, OFL
resources/gobos) go in gobos.zip - only the ones a bundled fixture uses.

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
# where each library keeps its gobo pictures, from its fixtures folder
GOBO_DIRS = {"ofl": Path("..") / "resources" / "gobos", "qlc": Path("..") / "gobos"}
GOBO_SUBS = {"ofl": "resources/gobos", "qlc": "resources/gobos"}


def _clone(src: str, into: Path) -> tuple[Path, str]:
    url, sub = REPOS[src]
    dest = into / src
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none",
                    "--sparse", url, str(dest)], check=True)
    subprocess.run(["git", "sparse-checkout", "set", sub, GOBO_SUBS[src]], cwd=dest, check=True)
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


def gobo_refs(parsed: list[dict]) -> set[str]:
    """Every gobo picture ("qlc:Chauvet/gobo00001.svg", "ofl:10-circles")
    a parsed fixture's wheels name."""
    return {sl["img"] for item in parsed for m in item.get("modes") or [] for d in m.get("detail") or []
            for sl in d.get("slots") or [] if sl.get("img")}


def bundled_gobo_refs() -> set[str]:
    """The gobo pictures the bundled fixtures use."""
    refs: set[str] = set()
    for src in ("ofl", "qlc"):
        for row in fixlib.index(src):
            try:
                refs |= gobo_refs(fixlib.load(src, row["key"]))
            except Exception:                      # noqa: BLE001 - it was checked at build
                continue
    return refs


def build_gobos(refs: set[str], dirs: dict[str, Path], out: Path) -> dict:
    """gobos.zip: qlc/<path> and ofl/<name>.<svg|png>, as in the libraries."""
    found, missing = 0, []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for ref in sorted(refs):
            src, _, name = ref.partition(":")
            base = dirs.get(src)
            if base is None:
                continue
            if src == "qlc":
                path = base / name
            else:
                path = next((base / (name + ext) for ext in (".svg", ".png") if (base / (name + ext)).is_file()), base / name)
            if not path.is_file() or ".." in Path(name).parts:
                missing.append(ref)
                continue
            zf.writestr(f"{src}/{path.relative_to(base).as_posix()}", path.read_bytes())
            found += 1
        zf.writestr("meta.json", json.dumps({"gobos": found, "missing": len(missing),
                                             "built": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
    return {"gobos": found, "missing": missing}


def _meta(src: str, commit: str, count: int) -> dict:
    return {"source": fixlib.SOURCES[src]["name"], "repository": REPOS[src][0],
            "commit": commit, "fixtures": count, "licence": fixlib.SOURCES[src]["licence"],
            "built": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _snapshot(path: Path) -> dict[str, tuple[str, str]]:
    """key -> (name, content hash) of a built library zip ({} if none)."""
    import hashlib
    out: dict[str, tuple[str, str]] = {}
    try:
        with zipfile.ZipFile(path) as zf:
            names = {e["key"]: f"{e.get('manufacturer', '')} {e.get('model', '')}".strip()
                     for e in json.loads(zf.read("index.json"))}
            for key, name in names.items():
                try:
                    out[key] = (name, hashlib.sha1(zf.read("fixtures/" + key)).hexdigest())
                except KeyError:
                    out[key] = (name, "")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        pass
    return out


def changes(before: dict, after: dict) -> dict:
    """What a rebuild brought: new lights, changed files, ones gone."""
    return {"added": sorted(after[k][0] for k in after.keys() - before.keys()),
            "removed": sorted(before[k][0] for k in before.keys() - after.keys()),
            "changed": sorted(after[k][0] for k in after.keys() & before.keys() if after[k][1] != before[k][1])}


def summary_md(per_src: dict[str, dict]) -> str:
    lines = []
    for src, ch in per_src.items():
        name = fixlib.SOURCES[src]["name"]
        lines.append(f"### {name}: {len(ch['added'])} new, {len(ch['changed'])} updated, {len(ch['removed'])} gone")
        for word in ("added", "changed", "removed"):
            if ch[word]:
                shown = ch[word][:60]
                lines.append(f"- **{word}:** " + ", ".join(shown) + (f" and {len(ch[word]) - 60} more" if len(ch[word]) > 60 else ""))
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ofl", type=Path, help="an OFL checkout's fixtures/ folder")
    ap.add_argument("--qlc", type=Path, help="a QLC+ checkout's resources/fixtures/ folder")
    ap.add_argument("--gobos-only", action="store_true", help="only rebuild gobos.zip for the bundled fixtures")
    ap.add_argument("--summary", type=Path, help="write what changed (markdown) here; "
                    "'NO CHANGES' on its first line when no fixture file changed")
    args = ap.parse_args()
    fixlib.BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    if args.gobos_only:
        if not (args.ofl and args.qlc):
            ap.error("--gobos-only needs --ofl and --qlc (checkouts with their gobos folders)")
        return _gobos({"ofl": args.ofl, "qlc": args.qlc})
    folders = {}
    per_src: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for src, given, build in (("ofl", args.ofl, build_ofl), ("qlc", args.qlc, build_qlc)):
            if given:
                folder, commit = given, _commit(given)
            else:
                print(f"* fetching {fixlib.SOURCES[src]['name']}...")
                folder, commit = _clone(src, tmp)
            out = fixlib.BUNDLE_DIR / fixlib.SOURCES[src]["file"]
            folders[src] = folder
            before = _snapshot(out)
            done = build(folder, commit, out)
            per_src[src] = changes(before, _snapshot(out))
            size = out.stat().st_size / 1e6
            print(f"* {fixlib.SOURCES[src]['name']}: {done['fixtures']} fixtures, "
                  f"{size:.1f} MB -> {out.relative_to(ROOT)}")
            for line in done["skipped"][:20]:
                print(f"    skipped {line}")
        fixlib._INDEX.clear()
        fixlib._BUILT.clear()
        if args.summary:
            moved = any(v for ch in per_src.values() for v in ch.values())
            args.summary.write_text(("" if moved else "NO CHANGES\n") + summary_md(per_src), encoding="utf-8")
            print(f"* what changed -> {args.summary}" + ("" if moved else " (nothing)"))
        return _gobos(folders)


def _gobos(fixture_dirs: dict[str, Path]) -> int:
    refs = bundled_gobo_refs()
    dirs = {src: (d / GOBO_DIRS[src]).resolve() for src, d in fixture_dirs.items()}
    out = fixlib.BUNDLE_DIR / "gobos.zip"
    done = build_gobos(refs, dirs, out)
    print(f"* gobos: {done['gobos']} pictures, {out.stat().st_size / 1e6:.1f} MB -> {out.relative_to(ROOT)}"
          + (f"; {len(done['missing'])} named but not in the library" if done["missing"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
