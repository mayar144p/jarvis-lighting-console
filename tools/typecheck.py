"""The type check: the desk's modules that are fully typed, checked by mypy.

    pip install mypy
    python tools/typecheck.py

The engine's parts (engine_*.py) are mixins that only make sense together,
so they aren't on the list yet; a module joins it once it checks clean, and
CI keeps it that way.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLEAN = [
    "aiexamples", "artnet", "autopilot", "beamlook", "config", "djrequests", "dmxin", "doctor", "engine_support",
    "fixture_kind", "fx", "gdtfshare", "link", "llm", "motion", "netif", "osc", "procs", "profiles", "rdm",
    "roomsense", "sacn", "searchmatch", "showpack", "showreview", "showtemplates", "streamdeck", "tempo", "websearch",
]


def run() -> int:
    files = [str(ROOT / "app" / f"{m}.py") for m in CLEAN]
    r = subprocess.run([sys.executable, "-m", "mypy", "--ignore-missing-imports", "--follow-imports=silent",
                        "--no-error-summary", "--disable-error-code", "annotation-unchecked", *files], cwd=ROOT, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if out:
        print(out)
    print("type check:", "clean" if r.returncode == 0 else "problems above", f"({len(files)} modules)")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(run())
