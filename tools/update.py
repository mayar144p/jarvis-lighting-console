"""Bring Jarvis up to date before it starts (run.bat / run.sh call this).

Safe by construction: it only ever FAST-FORWARDS the branch you are on to
its upstream, so it never merges, never rewrites anything and never
touches your data (data/, .env, show files and fixtures are not tracked).
It stands aside, and Jarvis starts on the version you have, when:

  * AUTO_UPDATE=false is in .env,
  * this is not a git checkout, or git is not installed,
  * there is no network (a venue with no internet is normal),
  * you have edited Jarvis's own files, or have commits of your own.

It always exits 0: an update problem must never stop the desk starting.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FETCH_TIMEOUT = 20
GENERATED = ("desktop/package-lock.json",)


def _enabled(root: Path) -> bool:
    env = root / ".env"
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "AUTO_UPDATE":
                return value.strip().strip('"\'').lower() not in ("0", "false", "no", "off")
    except OSError:
        pass
    return os.environ.get("AUTO_UPDATE", "true").lower() not in ("0", "false", "no", "off")


def _run_git(*args: str, timeout: float = 10, root: Path = ROOT) -> tuple[int, str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    try:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True,
                              text=True, timeout=timeout, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return done.returncode, (done.stdout or done.stderr or "").strip()


def update(root: Path = ROOT) -> str:
    """Try to update; returns one line saying what happened."""
    def _git(*args, timeout=10):
        return _run_git(*args, timeout=timeout, root=root)
    if not _enabled(root):
        return "auto-update is off (AUTO_UPDATE=false in .env)"
    if not (root / ".git").exists():
        return "not a git checkout - update by downloading the new version"
    code, _ = _git("--version")
    if code:
        return "git is not installed - skipping the update check"
    code, upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if code:
        return "this branch has no upstream - skipping the update check"
    code, dirty = _git("status", "--porcelain", "--untracked-files=no")
    # files npm writes by itself (the desktop app's `npm install` rewrites its
    # lockfile) are not the operator's edits: put them back, or one install
    # would stop every update after it
    changed = [line.split(None, 1)[-1].strip('"') for line in dirty.splitlines() if line.strip()] if code == 0 else []
    made = [f for f in changed if f in GENERATED]
    if made:
        _git("checkout", "--", *made)
        code, dirty = _git("status", "--porcelain", "--untracked-files=no")
    if code == 0 and dirty:
        files = [line.split(None, 1)[-1].strip('"') for line in dirty.splitlines() if line.strip()]
        return ("Jarvis's own files have local edits - not updating (your data is safe): "
                + ", ".join(files[:3]) + (f" and {len(files) - 3} more" if len(files) > 3 else ""))
    code, out = _git("fetch", "--quiet", timeout=FETCH_TIMEOUT)
    if code:
        return "no connection to the update server - starting the version you have"
    code, counts = _git("rev-list", "--left-right", "--count", "HEAD...@{u}")
    try:
        ahead, behind = (int(x) for x in counts.split())
    except ValueError:
        return "could not compare versions - starting the version you have"
    if behind == 0:
        return "up to date"
    if ahead:
        return (f"you have {ahead} commit(s) of your own - not updating "
                f"automatically ({behind} new on {upstream})")
    _, before = _git("rev-parse", "--short", "HEAD")
    code, out = _git("merge", "--ff-only", "--quiet", "@{u}", timeout=60)
    if code:
        return f"update failed, starting the version you have: {out.splitlines()[-1] if out else ''}"
    _, after = _git("rev-parse", "--short", "HEAD")
    _, log = _git("log", "--oneline", "--no-decorate", f"{before}..{after}")
    lines = log.splitlines()
    summary = "\n".join("    " + line.split(" ", 1)[-1] for line in lines[:8])
    more = f"\n    ...and {len(lines) - 8} more" if len(lines) > 8 else ""
    return f"updated {before} -> {after} ({len(lines)} change(s)):\n{summary}{more}"


def main() -> int:
    try:
        print("* update: " + update())
    except Exception as exc:                # noqa: BLE001 - never block start
        print(f"* update: skipped ({exc})")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
