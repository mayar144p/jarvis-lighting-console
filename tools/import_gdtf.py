"""Import GDTF fixture files into the Jarvis database.

Usage:
    python tools/import_gdtf.py                 # imports jarvis/fixtures_inbox/
    python tools/import_gdtf.py path/to/files   # imports any folder
    python tools/import_gdtf.py file.gdtf       # imports a single file

Get fixture files from https://gdtf-share.com (free account, manufacturer
uploads) - there is a "download entire library" option, or grab just the
brands you use.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, fixtures  # noqa: E402


def run(db_path: Path, folder: Path) -> dict:
    if not folder.exists():
        return {"error": f"{folder} does not exist", "imported": []}
    if folder.is_file():
        try:
            return fixtures.import_file(db_path, folder)
        except Exception as exc:  # noqa: BLE001
            return {"file": folder.name, "error": str(exc)}

    items = fixtures.import_directory(db_path, folder)
    return {
        "scanned": items["scanned"],
        "imported": items["imported"],
        "errors": items["errors"],
        "total_fixtures": fixtures.count(db_path),
    }


def main() -> None:
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else config.INBOX
    # Opt-in, like the server.  Seeding here would quietly re-add the four
    # generic guesses to a library the operator has deliberately emptied.
    if config.FIXTURE_SEED_BUILTINS:
        fixtures.seed_generics(config.DB_PATH)
    summary = run(config.DB_PATH, target)
    print(f"database: {config.DB_PATH}")
    if summary.get("error"):
        print(f"ERROR: {summary['error']}")
        return
    for item in summary.get("imported", []):
        for fix in item.get("imported", []):
            print(f"  + {fix['manufacturer']} {fix['model']} ({fix['modes']} mode(s))"
                  f"  [from {item['file']}]")
    for err in summary.get("errors", []):
        print(f"  ! {err['file']}: {err['error']}")
    print(f"scanned {summary.get('scanned', 0)} file(s), "
          f"database now has {summary.get('total_fixtures')} fixture(s)")


if __name__ == "__main__":
    main()
