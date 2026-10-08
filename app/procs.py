"""Running another program (ipconfig, nvidia-smi...) that can never hold
the desk up.

`subprocess.run(..., timeout=)` is not enough: when the time is up it kills
the program it started, then waits for the program's output to close - and
on Windows a helper the program started itself (git, ipconfig and friends
often hand their work to one) keeps that output open, so the wait never
ends.  That froze "Report a problem" on a Windows desk.  Here the program
starts in its own process group, the WHOLE group is stopped on timeout, and
the output is given up on rather than waited for.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

_WIN = sys.platform == "win32"


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _stop_all(proc: subprocess.Popen) -> None:
    """Stop the program and everything it started."""
    try:
        if _WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.killpg(proc.pid, 9)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run(cmd: list[str], timeout: float, cwd=None) -> Result:
    """Run `cmd`, at most `timeout` seconds; never raises for the program's
    own failure (returncode -1 and timed_out when it took too long, 127 when
    it isn't there)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if _WIN else 0
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=flags,
                                start_new_session=not _WIN)
    except OSError as exc:
        return Result(127, "", str(exc))
    try:
        out, err = proc.communicate(timeout=timeout)
        return Result(proc.returncode, out.decode(errors="replace"), err.decode(errors="replace"))
    except subprocess.TimeoutExpired:
        _stop_all(proc)
        try:
            out, err = proc.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            out, err = b"", b""            # something still holds the output: give up on it
            for f in (proc.stdout, proc.stderr):
                try:
                    f.close()
                except OSError:
                    pass
        return Result(-1, (out or b"").decode(errors="replace"), (err or b"").decode(errors="replace"), True)
