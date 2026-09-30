"""The autopilot in the desk (app/autopilot.py): a cue list as a pool of
looks, changed every phrase on the beat clock, by the room's energy.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import math
import threading
import time

from app import autopilot as ap_mod

TICK = 0.05


class AutopilotMixin:
    def _ap(self) -> dict:
        ap = self.__dict__.get("autopilot")
        if ap is None:
            ap = self.autopilot = {"on": False, "playback": 1, "bars": 16, "follow_sound": True,
                                   "on_drop": True, "next_bar": None, "history": [], "last": None}
        return ap

    def autopilot_public(self) -> dict:
        ap = self._ap()
        out = {k: ap[k] for k in ("on", "playback", "bars", "follow_sound", "on_drop", "last")}
        if ap["on"] and ap["next_bar"] is not None:
            bar = self._tempo().beats(time.monotonic()) / 4.0
            out["bars_left"] = max(0, int(math.ceil(ap["next_bar"] - bar)))
        out["room"] = ap_mod.room_tier(self._room_energy())
        return out

    def _room_energy(self) -> float | None:
        """How loud the room has been lately (0..1), or None with no sound."""
        if self._sound_live(time.monotonic()) is None:
            return None
        return self.__dict__.get("_sound_energy")

    # -- actions ------------------------------------------------------------
    def _a_autopilot(self, state=None, playback=None, bars=None, follow_sound=None, on_drop=None, **_):
        """Run a cue list by itself: every `bars` bars another of its cues,
        by the room's energy (follow_sound) and the biggest on a drop."""
        ap = self._ap()
        if playback is not None:
            n = int(playback)
            if not 1 <= n <= len(self.playbacks):
                raise ValueError(f"no playback {n}")
            ap["playback"] = n
        if bars is not None:
            if int(bars) not in ap_mod.BARS:
                raise ValueError("bars is 4, 8, 16 or 32")
            ap["bars"] = int(bars)
        if follow_sound is not None:
            ap["follow_sound"] = str(follow_sound).lower() in ("1", "true", "on", "yes")
        if on_drop is not None:
            ap["on_drop"] = str(on_drop).lower() in ("1", "true", "on", "yes")
        if state is not None:
            want = str(state).lower() in ("1", "true", "on", "yes", "start")
            if want and len(self.playbacks[ap["playback"] - 1]["stack"]) < 2:
                raise ValueError(f"playback {ap['playback']} needs at least 2 cues (looks) for the autopilot")
            ap["on"] = want
            ap["next_bar"] = None
            ap["history"] = []
            if want:
                self._ap_start()
        pb = self.playbacks[ap["playback"] - 1]
        return {"autopilot": self.autopilot_public(),
                "summary": (f"autopilot on: {pb.get('name') or 'playback ' + str(ap['playback'])}, a new look every {ap['bars']} bars"
                            if ap["on"] else "autopilot off")}

    def _a_autopilot_next(self, biggest=False, **_):
        """Change the look now (biggest: the biggest one)."""
        ap = self._ap()
        if not ap["on"]:
            raise ValueError("the autopilot is off")
        pick = self._ap_pick("now", biggest=bool(biggest))
        if pick is None:
            raise ValueError("nothing to change to")
        self._ap_go(pick)
        return {"autopilot": self.autopilot_public(), "summary": f"autopilot: {ap['last']['name']}"}

    # -- the ticker -----------------------------------------------------------
    def _ap_pick(self, reason: str, biggest: bool = False) -> tuple[int, int] | None:
        ap = self._ap()
        pb = self.playbacks[ap["playback"] - 1]
        stack = pb.get("stack") or []
        if len(stack) < 2:
            return None
        want = ap_mod.room_tier(self._room_energy()) if ap["follow_sound"] else None
        k = ap_mod.choose(stack, pb["index"] if pb["active"] else -1, want, ap["history"], biggest=biggest)
        if k is None:
            return None
        ap["history"] = (ap["history"] + [k])[-8:]
        name = stack[k].get("name") or f"cue {k + 1}"
        ap["last"] = {"cue": k + 1, "name": name, "why": reason, "tier": ap_mod.tiers(stack)[k]}
        return ap["playback"], k + 1

    def _ap_go(self, pick: tuple[int, int]) -> None:
        self.act("cue_go", playback=pick[0], cue=pick[1])

    def ap_tick(self, now: float | None = None) -> tuple[int, int] | None:
        """Once per tick: a phrase boundary passed? (also the tests' entry)."""
        with self.lock:
            ap = self._ap()
            if not ap["on"]:
                return None
            now = time.monotonic() if now is None else now
            bar = self._tempo().beats(now) / 4.0
            bars = ap["bars"]
            if ap["next_bar"] is None:
                ap["next_bar"] = (math.floor(bar / bars) + 1) * bars
                return None
            if bar < ap["next_bar"]:
                return None
            while ap["next_bar"] <= bar:
                ap["next_bar"] += bars
            pick = self._ap_pick("phrase")
        if pick:
            self._ap_go(pick)
        return pick

    def ap_drop(self) -> None:
        """The sound heard a drop: the biggest look, now."""
        with self.lock:
            ap = self._ap()
            if not (ap["on"] and ap["on_drop"]):
                return
            pick = self._ap_pick("drop", biggest=True)
            bar = self._tempo().beats(time.monotonic()) / 4.0
            ap["next_bar"] = (math.floor(bar / ap["bars"]) + 1) * ap["bars"]
        if pick:
            self._ap_go(pick)

    def _ap_start(self) -> None:
        th = self.__dict__.get("_ap_thread")
        if th is not None and th.is_alive():
            return
        stop = self._ap_stop_ev = threading.Event()

        def loop():
            while not stop.wait(TICK):
                if not self._ap()["on"]:
                    break
                try:
                    self.ap_tick()
                except Exception as exc:            # noqa: BLE001 - never die silently
                    self.output["last_error"] = f"autopilot: {exc}"

        th = self._ap_thread = threading.Thread(target=loop, name="jarvis-autopilot", daemon=True)
        th.start()

    def _ap_shutdown(self) -> None:
        ev = self.__dict__.get("_ap_stop_ev")
        if ev:
            ev.set()

