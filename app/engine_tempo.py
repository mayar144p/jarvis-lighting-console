"""The beat clock (app/tempo.py) in the desk: tap, typed BPM, MIDI clock,
Pro DJ Link from the CDJs; the Speed master following the tempo; effects
locked to the beat.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import socket
import threading
import time

from app import tempo as tempo_mod
from app.engine_base import _truthy

# effects can lock one cycle to this many beats
BEAT_CHOICES = (0.25, 0.5, 1, 2, 4, 8, 16, 32)


class TempoMixin:
    def _tempo(self) -> tempo_mod.Clock:
        clock = self.__dict__.get("tempo")
        if clock is None:
            clock = self.tempo = tempo_mod.Clock(now=time.monotonic())
            self.tempo_follow = True
        return clock

    def _tempo_changed(self) -> None:
        """The Speed master follows the tempo (120 BPM = 1x) unless told not to."""
        if self.__dict__.get("tempo_follow", True):
            self.speed_master = max(0.05, min(4.0, self._tempo().bpm / 120.0))

    def _tempo_result(self, summary: str) -> dict:
        return {"tempo": self.tempo_public(), "speed_master": self.speed_master, "summary": summary}

    def tempo_public(self) -> dict:
        out = self._tempo().public(time.monotonic())
        out["follow"] = bool(self.__dict__.get("tempo_follow", True))
        out["prodj"] = bool(self.__dict__.get("_prodj_thread"))
        dj = self.__dict__.get("_prodj_last")
        if dj:
            out["deck"] = dj
        return out

    # -- actions --------------------------------------------------------------
    def _a_tempo_tap(self, **_):
        """One tap on the beat (the tempo from 2 taps on; each tap is a beat)."""
        bpm = self._tempo().tap(time.monotonic())
        self.tempo_follow = True             # tapping means "go at this speed"
        if bpm is None:
            return self._tempo_result("tap again on the next beat")
        self._tempo_changed()
        return self._tempo_result(f"{bpm:.1f} BPM (tapped)")

    def _a_tempo_set(self, bpm=None, follow=None, source="manual", **_):
        """Set the tempo (and whether the Speed master follows it)."""
        now = time.monotonic()
        if follow is not None:
            self.tempo_follow = _truthy(follow)
        if bpm is not None:
            try:
                value = float(bpm)
            except (TypeError, ValueError):
                raise ValueError(f"bad BPM: {bpm!r}") from None
            self._tempo().set_bpm(value, now, str(source or "manual"))
        self._tempo_changed()
        t = self._tempo()
        return self._tempo_result(f"{t.bpm:.1f} BPM" + ("" if self.tempo_follow else " (speed master free)"))

    def _a_tempo_sync(self, beat=1, **_):
        """Now is beat 1 of the bar (or `beat`): line the effects up with the music."""
        now = time.monotonic()
        if int(beat or 1) == 1:
            self._tempo().downbeat(now)
        else:
            self._tempo().align(now, int(beat))
        return self._tempo_result(f"beat {int(beat or 1)} is now")

    def _a_tempo_nudge(self, bpm=None, beats=None, **_):
        """Nudge the tempo by ±bpm, or the phase by ±beats (e.g. 0.1)."""
        t = self._tempo()
        now = time.monotonic()
        if bpm is not None:
            t.set_bpm(t.bpm + float(bpm), now, t.source if t.source in ("manual", "tap") else "manual")
            self._tempo_changed()
        if beats is not None:
            t.anchor -= float(beats) * 60.0 / t.bpm
        return self._tempo_result(f"{t.bpm:.1f} BPM")

    def _a_tempo_prodj(self, state=None, **_):
        """Listen for the CDJs' beat packets (Pro DJ Link, UDP 50001)."""
        want = (not self.__dict__.get("_prodj_thread")) if state is None else _truthy(state)
        if want:
            err = self._prodj_start()
            if err:
                raise ValueError(err)
            return self._tempo_result("listening to the CDJs (Pro DJ Link)")
        self._prodj_stop()
        return self._tempo_result("stopped listening to the CDJs")

    # -- feeds that are not actions (no undo step, no reload per tick) ---------
    def tempo_midi(self, status: int) -> None:
        """A MIDI real-time byte: 0xF8 clock tick, 0xFA start, 0xFB continue, 0xFC stop."""
        with self.lock:
            t = self._tempo()
            before = round(t.bpm, 1)
            now = time.monotonic()
            if status == 0xF8:
                t.midi_tick(now)
            elif status == 0xFA:
                t.midi_start(now)
            if round(t.bpm, 1) != before:
                self._tempo_changed()

    def tempo_dj(self, beat: dict) -> None:
        with self.lock:
            t = self._tempo()
            before = round(t.bpm, 1)
            t.dj_beat(time.monotonic(), beat["bpm"], beat["beat"])
            self._prodj_last = {"name": beat.get("name"), "device": beat.get("device"), "bpm": beat["bpm"]}
            if round(t.bpm, 1) != before:
                self._tempo_changed()

    # -- Pro DJ Link listener ----------------------------------------------------
    def _prodj_start(self, port: int | None = None) -> str | None:
        if self.__dict__.get("_prodj_thread"):
            return None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)   # rekordbox may listen too
            except (AttributeError, OSError):
                pass
            sock.bind(("0.0.0.0", int(port or tempo_mod.PRODJ_PORT)))
            sock.settimeout(0.3)
        except OSError as exc:
            return f"can't listen for the CDJs on UDP {port or tempo_mod.PRODJ_PORT}: {exc}"
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                try:
                    pkt, _peer = sock.recvfrom(1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                beat = tempo_mod.parse_prodj_beat(pkt)
                if beat:
                    try:
                        self.tempo_dj(beat)
                    except Exception:                  # noqa: BLE001 - never stop listening
                        pass
            sock.close()

        th = threading.Thread(target=loop, name="jarvis-prodj", daemon=True)
        self._prodj_thread, self._prodj_stopper, self._prodj_sock = th, stop, sock
        th.start()
        return None

    def _prodj_stop(self) -> None:
        stop = self.__dict__.get("_prodj_stopper")
        th = self.__dict__.get("_prodj_thread")
        if stop:
            stop.set()
        if th and th is not threading.current_thread():
            th.join(1.0)
        self._prodj_thread = self._prodj_stopper = None
