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

from app import link as link_mod
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
        fol = self.__dict__.get("_link_follower")
        if self.__dict__.get("_link_thread") and fol is not None:
            now_us = int(time.monotonic() * 1e6)
            out["link"] = {"peers": fol.connected(now_us), "synced": fol.offset is not None}
        dj = self.__dict__.get("_prodj_last")
        if dj:
            out["deck"] = dj
        out["bar_in_phrase"] = self._tempo().bar_in_phrase(time.monotonic())
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

    def _a_tempo_phrase(self, **_):
        """The phrase starts now (8 bars): buttons set to "on the next
        phrase" fire 8 bars on, and the tempo panel counts the bars."""
        self._tempo().mark_phrase(time.monotonic())
        return self._tempo_result("phrase starts here: bar 1 of 8")

    def _a_tempo_link(self, state=None, **_):
        """Follow an Ableton Link session on the network: its tempo and
        its beat (UDP multicast 224.76.78.75:20808)."""
        want = (not self.__dict__.get("_link_thread")) if state is None else _truthy(state)
        if want:
            err = self._link_start()
            if err:
                raise ValueError(err)
            return self._tempo_result("following Ableton Link")
        self._link_stop()
        return self._tempo_result("stopped following Ableton Link")

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

    def tempo_dj_status(self, st: dict) -> None:
        """A CDJ's status (UDP 50002): who's master, who's playing."""
        with self.lock:
            self._decks().saw_status(st, time.monotonic())

    def _decks(self) -> "tempo_mod.DeckFollower":
        d = self.__dict__.get("_deck_follower")
        if d is None:
            d = self._deck_follower = tempo_mod.DeckFollower()
        return d

    def tempo_dj(self, beat: dict) -> None:
        with self.lock:
            if not self._decks().take(beat.get("device", 0), time.monotonic()):
                return                         # another deck: the master (or the one followed) leads
            t = self._tempo()
            before = round(t.bpm, 1)
            t.dj_beat(time.monotonic(), beat["bpm"], beat["beat"])
            m = self._decks().master(time.monotonic())
            self._prodj_last = {"name": beat.get("name"), "device": beat.get("device"), "bpm": beat["bpm"],
                                "master": m is not None and m == beat.get("device")}
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
        # the players' status (who's master) on 50002 - if the port is free
        status_port = (int(port) + 1) if port else tempo_mod.PRODJ_STATUS_PORT
        try:
            ss = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ss.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                ss.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            ss.bind(("0.0.0.0", status_port))
            ss.settimeout(0.3)
        except OSError:
            ss = None                          # beats alone still work: the deck followed stays until it stops

        def status_loop():
            while not stop.is_set():
                try:
                    pkt, _peer = ss.recvfrom(1500)
                except socket.timeout:
                    continue
                except OSError:
                    break
                st = tempo_mod.parse_prodj_status(pkt)
                if st:
                    try:
                        self.tempo_dj_status(st)
                    except Exception:              # noqa: BLE001 - never stop listening
                        pass
            ss.close()
        if ss is not None:
            threading.Thread(target=status_loop, name="jarvis-prodj-status", daemon=True).start()
        return None

    def _prodj_stop(self) -> None:
        stop = self.__dict__.get("_prodj_stopper")
        th = self.__dict__.get("_prodj_thread")
        if stop:
            stop.set()
        if th and th is not threading.current_thread():
            th.join(1.0)
        self._prodj_thread = self._prodj_stopper = None

    # -- Ableton Link listener -----------------------------------------------------
    def tempo_link_feed(self, fol: link_mod.Follower) -> None:
        """The session's tempo (and beat, once measured) into the clock."""
        bpm = fol.bpm()
        if not bpm:
            return
        with self.lock:
            t = self._tempo()
            # a CDJ or MIDI clock that is playing right now wins
            if t.live(time.monotonic()) and t.source in ("midi", "prodj"):
                return
            before = round(t.bpm, 1)
            now = time.monotonic()
            t.link_sync(now, bpm, fol.beats(int(now * 1e6)))
            if round(t.bpm, 1) != before:
                self._tempo_changed()

    def _link_start(self, port: int | None = None, group: str | None = None) -> str | None:
        if self.__dict__.get("_link_thread"):
            return None
        port = int(port or link_mod.PORT)
        try:
            disc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            disc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                disc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)    # Live on this computer listens too
            except (AttributeError, OSError):
                pass
            disc.bind(("0.0.0.0", port))
            try:
                mreq = socket.inet_aton(group or link_mod.GROUP) + socket.inet_aton("0.0.0.0")
                disc.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError:
                pass                       # no multicast route: peers that answer directly still count
            meas = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            meas.bind(("0.0.0.0", 0))
        except OSError as exc:
            return f"can't listen for Ableton Link on UDP {port}: {exc}"
        stop = threading.Event()
        fol = self._link_follower = link_mod.Follower()

        def loop():
            import select
            next_ping = 0.0
            prev_ghost = 0
            while not stop.is_set():
                try:
                    ready, _w, _x = select.select([disc, meas], [], [], 0.1)
                except (OSError, ValueError):
                    break
                now_us = int(time.monotonic() * 1e6)
                for s in ready:
                    try:
                        pkt, _peer = s.recvfrom(2048)
                    except OSError:
                        continue
                    if s is disc:
                        msg = link_mod.parse_discovery(pkt)
                        if msg:
                            fol.alive(msg, now_us)
                    else:
                        msg = link_mod.parse_pong(pkt)
                        if msg:
                            fol.pong(msg, int(time.monotonic() * 1e6))
                            prev_ghost = msg["ghost"]
                # measure the clocks: a few pings a second to the session's peers
                if time.monotonic() >= next_ping:
                    next_ping = time.monotonic() + (0.05 if fol.offset is None else 0.5)
                    for ep in fol.endpoints(now_us)[:3]:
                        try:
                            meas.sendto(link_mod.build_ping(int(time.monotonic() * 1e6), prev_ghost), ep)
                        except OSError:
                            pass
                if fol.timeline and fol.connected(now_us):
                    try:
                        self.tempo_link_feed(fol)
                    except Exception:              # noqa: BLE001 - never stop listening
                        pass
            disc.close()
            meas.close()

        th = threading.Thread(target=loop, name="jarvis-link", daemon=True)
        self._link_thread, self._link_stopper = th, stop
        th.start()
        return None

    def _link_stop(self) -> None:
        stop = self.__dict__.get("_link_stopper")
        th = self.__dict__.get("_link_thread")
        if stop:
            stop.set()
        if th and th is not threading.current_thread():
            th.join(1.0)
        self._link_thread = self._link_stopper = None

    # -- MIDI timecode: the timeline follows it ---------------------------------
    def _tc(self) -> tempo_mod.Timecode:
        tc = self.__dict__.get("timecode")
        if tc is None:
            tc = self.timecode = tempo_mod.Timecode()
            self.tc_follow = False
            self.tc_offset = 0.0
        return tc

    def timecode_public(self) -> dict:
        tc = self._tc()
        return {"follow": self.tc_follow, "offset": self.tc_offset, "running": tc.running(time.monotonic()),
                "time": tc.text(), "fps": tc.fps}

    def _a_timecode(self, state=None, offset=None, **_):
        """The timeline follows MIDI timecode (MTC) from the desk's MIDI
        input: it jumps where the timecode is and plays along; `offset`
        (seconds) is the timecode at which the timeline's 0 is."""
        self._tc()
        if offset is not None:
            self.tc_offset = float(offset)
        if state is not None:
            self.tc_follow = _truthy(state)
        if self.tc_follow:
            self._tc_watch()
        return {"timecode": self.timecode_public(),
                "summary": ("the timeline follows MIDI timecode" + (f" (0 = {self.tc_offset:g} s)" if self.tc_offset else ""))
                if self.tc_follow else "timecode off"}

    def tempo_mtc(self, data: int) -> None:
        """A quarter frame (0xF1) from the desk's MIDI input."""
        with self.lock:
            t = self._tc().quarter_frame(int(data), time.monotonic())
            if t is None or not self.tc_follow:
                return
            target = t - self.tc_offset
            if target < 0 or target > self.timeline["length"]:
                if self.tl["playing"]:
                    self._a_timeline_pause()
                return
            if not self.tl["playing"]:
                self._a_timeline_seek(t=target)
                self._a_timeline_play()
                self._tc_started = True
            elif abs(self._tl_now() - target) > 0.15:
                self._a_timeline_seek(t=target)

    def _tc_watch(self) -> None:
        """Timecode stopped (no quarter frame for 0.3 s): pause the timeline."""
        th = self.__dict__.get("_tc_thread")
        if th is not None and th.is_alive():
            return

        def loop():
            while self.__dict__.get("tc_follow"):
                time.sleep(0.1)
                with self.lock:
                    if self.__dict__.get("_tc_started") and self.tl["playing"] \
                            and not self._tc().running(time.monotonic()):
                        self._a_timeline_pause()
                        self._tc_started = False

        self._tc_thread = threading.Thread(target=loop, name="jarvis-mtc", daemon=True)
        self._tc_thread.start()
