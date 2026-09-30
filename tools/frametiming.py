"""Frame-timing check: does DMX go out on a steady beat with a big rig,
effects running and several screens watching?

    python tools/frametiming.py            # exit 1 when over budget

It patches 256 RGBW pars and 30 multi-head movers, runs an effect and a
cue, starts the real output thread (dry run: no network) and has three
"screens" pull the live look 30 times a second and a snapshot 4 times a
second, the way the browser's stream does.  Then it measures the gaps
between frames.

Budgets (on a CI runner; a show laptop does better):
  * one frame build under 12 ms (40 Hz leaves 25 ms);
  * 99% of gaps under 1.6 periods, none over 3.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config, fixlib, fixtures  # noqa: E402
from app import engine as eng  # noqa: E402

BUILD_BUDGET_MS = 12.0


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Eurolite/Eurolite-LED-PARty-RGBW.qxf"), "qlc")
        fixtures.store_parsed(db, fixlib.load("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"), "qlc")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            for u in (1, 2, 3, 4):
                e.act("add_heads", query="LED PARty RGBW", qty=64, universe=u)
            e.act("add_heads", query="Intimidator Wave 360", qty=15, universe=5)
            e.act("add_heads", query="Intimidator Wave 360", qty=15, universe=6)
            e.act("select_all")
            e.act("set_intensity", level=80)
            e.act("run_fx", name="rainbow")
            e.act("record_cue", playback=1)
            e.act("cue_go", playback=1)
            e.act("select_all")
            e.act("run_fx", name="rainbow")

            n = 40
            t0 = time.perf_counter()
            for _ in range(n):
                with e.lock:
                    e.build_frames()
            build_ms = (time.perf_counter() - t0) / n * 1000

            stamps: list[float] = []
            real = e.build_frames

            def stamped(*a, **k):
                stamps.append(time.monotonic())
                return real(*a, **k)
            e.build_frames = stamped
            e._start_output()
            stop = threading.Event()

            def screen():
                next_snap = 0.0
                while not stop.is_set():
                    e.look_text()
                    if time.monotonic() >= next_snap:
                        next_snap = time.monotonic() + 0.25
                        e.snapshot()
                    time.sleep(1 / 30)
            screens = [threading.Thread(target=screen, daemon=True) for _ in range(3)]
            for s in screens:
                s.start()
            time.sleep(0.5)
            stamps.clear()
            time.sleep(5)
            stop.set()
            for s in screens:
                s.join()
            gaps = sorted((b - a) * 1000 for a, b in zip(stamps, stamps[1:]))
        finally:
            e.shutdown()
    period = 1000.0 / config.DMX_HZ
    p99 = gaps[int(len(gaps) * 0.99)]
    worst = gaps[-1]
    print(f"{len(e.patch)} heads, 3 screens: build {build_ms:.1f} ms; "
          f"{len(gaps) + 1} frames in 5 s; gap p50 {gaps[len(gaps) // 2]:.1f} ms, "
          f"p99 {p99:.1f} ms, worst {worst:.1f} ms (period {period:.1f} ms)")
    bad = []
    if build_ms > BUILD_BUDGET_MS:
        bad.append(f"a frame takes {build_ms:.1f} ms to build (budget {BUILD_BUDGET_MS})")
    if p99 > period * 1.6:
        bad.append(f"1% of frames are more than {p99 - period:.0f} ms late")
    if worst > period * 3:
        bad.append(f"a frame was {worst - period:.0f} ms late")
    for b in bad:
        print("FAIL", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
