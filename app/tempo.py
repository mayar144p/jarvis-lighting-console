"""The beat clock: how fast the music is and where the beat is.

One clock for the whole desk, fed by whichever source is there: taps, a
typed BPM, MIDI clock (24 ticks a beat, Start = the downbeat), Pro DJ Link
beat packets from CDJs (tempo with the pitch fader, and which beat of the
bar), or the browser (Web MIDI clock, audio beat detection).  Effects that
are locked to it run one cycle per N beats, in phase with the downbeat.

`beats(now)` is the running beat count since the anchor; a tempo change
re-anchors so the count never jumps - an effect locked to the beat keeps
its place in the bar when the DJ nudges the pitch.

Pure: time is passed in (monotonic seconds), so it is tested directly.
"""
from __future__ import annotations

import math

MIN_BPM, MAX_BPM = 30.0, 300.0
SOURCES = ("manual", "tap", "midi", "prodj", "browser", "audio", "link")
# a source that has gone quiet this long no longer drives the clock
QUIET_S = 4.0


def _clamp_bpm(bpm: float) -> float:
    return max(MIN_BPM, min(MAX_BPM, float(bpm)))


class Clock:
    def __init__(self, bpm: float = 120.0, now: float = 0.0):
        self.bpm = _clamp_bpm(bpm)
        self.anchor = float(now)          # when beat 0 was
        self.source = "manual"
        self.heard_at: float | None = None  # last word from a live source
        self.bar = 4
        self._taps: list[float] = []
        self._ticks: list[float] = []

    # -- reading --------------------------------------------------------------
    def beats(self, now: float) -> float:
        # rounded to a billionth of a beat: lined up on a beat, the sums
        # land a hair under it (0.999999...) and read the beat before
        return round((now - self.anchor) * self.bpm / 60.0, 9)

    def beat_in_bar(self, now: float) -> int:
        """1..4"""
        return int(math.floor(self.beats(now))) % self.bar + 1

    def phase(self, now: float) -> float:
        """0..1 through the current beat."""
        b = self.beats(now)
        return b - math.floor(b)

    def live(self, now: float) -> bool:
        """A clock source (MIDI, CDJs, audio) is driving it right now."""
        return self.source in ("midi", "prodj", "browser", "audio", "link") and \
            self.heard_at is not None and now - self.heard_at < QUIET_S

    def public(self, now: float) -> dict:
        b = self.beats(now)
        return {"bpm": round(self.bpm, 2), "source": self.source, "live": self.live(now),
                "beat": int(math.floor(b)) % self.bar + 1, "phase": round(b - math.floor(b), 3)}

    # -- setting --------------------------------------------------------------
    def set_bpm(self, bpm: float, now: float, source: str = "manual") -> None:
        """A new tempo from here on: the beat count carries on from where it
        is (no jump), only its rate changes."""
        b = self.beats(now)
        self.bpm = _clamp_bpm(bpm)
        self.anchor = now - b * 60.0 / self.bpm
        self.source = source if source in SOURCES else "manual"
        if self.source not in ("manual", "tap"):
            self.heard_at = now

    def downbeat(self, now: float) -> None:
        """Now is beat 1 of a bar."""
        self.anchor = now

    PHRASE = 32                      # beats: 8 bars, the length dance music builds in

    def mark_phrase(self, now: float) -> None:
        """Now is the first beat of a phrase (8 bars).  The count restarts
        here, so "on the next phrase" means 8 bars from now - and a CDJ's
        beats keep it there (they move the count by less than a bar)."""
        self.anchor = now

    def bar_in_phrase(self, now: float) -> int:
        """1..8"""
        return int(math.floor(self.beats(now) / self.bar)) % (self.PHRASE // self.bar) + 1

    def align(self, now: float, beat_in_bar: int) -> None:
        """Now is this beat of the bar (1..4): move the phase to the nearest
        count that says so, without changing the tempo."""
        b = self.beats(now)
        want = (int(beat_in_bar) - 1) % self.bar
        base = math.floor(b) - (math.floor(b) % self.bar) + want
        best = min((base - self.bar, base, base + self.bar), key=lambda n: abs(n - b))
        self.anchor = now - best * 60.0 / self.bpm

    def tap(self, now: float) -> float | None:
        """One tap on the beat.  The first tap of a run is beat 1; from the
        second tap on (within 2 s of the last) the tempo is the average gap,
        and each tap is a beat, so the phase follows the hand.  Returns the
        BPM once there is one."""
        if self._taps and now - self._taps[-1] > 2.0:
            self._taps = []
        if self._taps and now - self._taps[-1] < 0.06:
            # a bounce (a doubled key or MIDI message, or two taps inside one
            # tick of Windows' 16 ms clock): 1000 BPM is not a tempo, and a
            # zero gap divided by zero
            return self.bpm if len(self._taps) > 1 else None
        self._taps.append(now)
        self._taps = self._taps[-8:]
        if len(self._taps) < 2:
            self.downbeat(now)                 # the first tap of a run is "1"
            return None
        gaps = [b - a for a, b in zip(self._taps, self._taps[1:])]
        bpm = 60.0 / (sum(gaps) / len(gaps))
        self.set_bpm(bpm, now, "tap")
        self.align_beat(now)
        return self.bpm

    def align_beat(self, now: float) -> None:
        """Now is on a beat (whichever): snap the phase to the nearest beat."""
        b = self.beats(now)
        self.anchor = now - round(b) * 60.0 / self.bpm

    # -- MIDI clock: 24 ticks a beat ------------------------------------------
    def midi_tick(self, now: float) -> None:
        if self._ticks and now - self._ticks[-1] > 1.0:
            self._ticks = []                   # the clock stopped and came back
        self._ticks.append(now)
        self._ticks = self._ticks[-97:]        # four beats
        self.heard_at = now
        if len(self._ticks) >= 25:
            span = self._ticks[-1] - self._ticks[0]
            bpm = 60.0 / (span / (len(self._ticks) - 1) * 24)
            # small wobble in tick timing is not a tempo change
            if abs(bpm - self.bpm) > 0.15 or self.source != "midi":
                self.set_bpm(round(bpm, 2), now, "midi")
            self.source = "midi"

    def midi_start(self, now: float) -> None:
        self._ticks = []
        self.downbeat(now)
        self.source = "midi"
        self.heard_at = now

    # -- Pro DJ Link: a CDJ's beat packet -------------------------------------
    def dj_beat(self, now: float, bpm: float, beat_in_bar: int) -> None:
        if abs(bpm - self.bpm) > 0.05 or self.source != "prodj":
            self.set_bpm(bpm, now, "prodj")
        self.align(now, beat_in_bar)
        self.source = "prodj"
        self.heard_at = now


    # -- Ableton Link: the session's tempo and beat ---------------------------
    def link_sync(self, now: float, bpm: float, beat: float | None) -> None:
        """Follow a Link session: its tempo, and (once the clocks are
        measured) its place in the bar - moved to the nearest count with
        the same phase, so a locked effect never jumps a bar."""
        if abs(bpm - self.bpm) > 0.01 or self.source != "link":
            self.set_bpm(bpm, now, "link")
        if beat is not None:
            cur = self.beats(now)
            d = (beat - cur) % self.bar
            if d > self.bar / 2:
                d -= self.bar
            self.anchor = now - (cur + d) * 60.0 / self.bpm
        self.source = "link"
        self.heard_at = now


# ---------------------------------------------------------------------------
# Pro DJ Link beat packets (UDP 50001), as the CDJ / XDJ / DJM send them
# ---------------------------------------------------------------------------
PRODJ_PORT = 50001
PRODJ_MAGIC = b"Qspt1WmJOL"
PRODJ_BEAT = 0x28


def parse_prodj_beat(pkt: bytes) -> dict | None:
    """{device, name, bpm (with the pitch fader), beat (1..4)} or None."""
    if len(pkt) < 0x60 or pkt[:10] != PRODJ_MAGIC or pkt[0x0A] != PRODJ_BEAT:
        return None
    name = pkt[0x0B:0x1F].split(b"\0", 1)[0].decode("ascii", "replace").strip()
    device = pkt[0x21]
    pitch = int.from_bytes(pkt[0x55:0x58], "big")          # 0x100000 = 0 %
    track_bpm = int.from_bytes(pkt[0x5A:0x5C], "big") / 100.0
    beat = pkt[0x5C]
    if not track_bpm or not 1 <= beat <= 4:
        return None
    return {"device": device, "name": name, "bpm": round(track_bpm * pitch / 0x100000, 2), "beat": beat}


# CDJ status packets (UDP 50002): which player is the tempo MASTER, which
# are playing.  Two decks playing both send beats; the desk follows the
# master (the deck the DJ syncs the others to), not whichever spoke last.
PRODJ_STATUS_PORT = 50002
PRODJ_STATUS = 0x0A
F_PLAYING, F_MASTER, F_SYNC, F_ONAIR = 0x40, 0x20, 0x10, 0x08


def parse_prodj_status(pkt: bytes) -> dict | None:
    """{device, name, playing, master, synced, on_air} or None."""
    if len(pkt) < 0x8A or pkt[:10] != PRODJ_MAGIC or pkt[0x0A] != PRODJ_STATUS:
        return None
    flags = pkt[0x89]
    return {"device": pkt[0x21], "name": pkt[0x0B:0x1F].split(b"\0", 1)[0].decode("ascii", "replace").strip(),
            "playing": bool(flags & F_PLAYING), "master": bool(flags & F_MASTER),
            "synced": bool(flags & F_SYNC), "on_air": bool(flags & F_ONAIR)}


def build_prodj_status(device: int, master: bool = False, playing: bool = True, name: str = "CDJ-3000") -> bytes:
    p = bytearray(0xD4)
    p[:10] = PRODJ_MAGIC
    p[0x0A] = PRODJ_STATUS
    p[0x0B:0x0B + len(name)] = name.encode("ascii")[:20]
    p[0x21] = device & 0xFF
    p[0x89] = (F_PLAYING if playing else 0) | (F_MASTER if master else 0)
    return bytes(p)


class DeckFollower:
    """Which deck's beats drive the clock: the master when the players say
    who it is (status within MASTER_S); else the deck already followed,
    until it goes quiet for QUIET_S; else whichever deck is playing."""
    MASTER_S, QUIET_S = 3.0, 2.0

    def __init__(self):
        self.status: dict[int, dict] = {}     # device -> its last status, with "at"
        self.last_beat: dict[int, float] = {}
        self.following: int | None = None

    def saw_status(self, st: dict, now: float) -> None:
        self.status[st["device"]] = dict(st, at=now)

    def master(self, now: float) -> int | None:
        for dev, st in self.status.items():
            if st.get("master") and now - st["at"] < self.MASTER_S:
                return dev
        return None

    def take(self, device: int, now: float) -> bool:
        """A beat from `device`: should the clock follow it?"""
        self.last_beat[device] = now
        m = self.master(now)
        if m is not None:
            self.following = m
            return device == m
        cur = self.following
        if cur is None or cur == device or now - self.last_beat.get(cur, -1e9) > self.QUIET_S:
            self.following = device
            return True
        return False


def build_prodj_beat(device: int, bpm: float, beat: int, pitch_pct: float = 0.0, name: str = "CDJ-3000") -> bytes:
    """A beat packet as a CDJ sends it (tests, and the virtual deck)."""
    p = bytearray(0x60)
    p[:10] = PRODJ_MAGIC
    p[0x0A] = PRODJ_BEAT
    p[0x0B:0x0B + len(name)] = name.encode("ascii")[:20]
    p[0x1F] = 0x01
    p[0x21] = device & 0xFF
    p[0x55:0x58] = int(0x100000 * (1 + pitch_pct / 100.0)).to_bytes(3, "big")
    p[0x5A:0x5C] = int(round(bpm * 100)).to_bytes(2, "big")
    p[0x5C] = beat
    p[0x5F] = device & 0xFF
    return bytes(p)


# ---------------------------------------------------------------------------
# MIDI timecode (MTC): eight quarter-frame messages (0xF1) make one time
# ---------------------------------------------------------------------------
MTC_RATES = {0: 24.0, 1: 25.0, 2: 29.97, 3: 30.0}


class Timecode:
    def __init__(self):
        self.pieces = [None] * 8
        self.seconds: float | None = None
        self.fps = 25.0
        self.at: float | None = None          # when the last quarter frame came

    def quarter_frame(self, data: int, now: float) -> float | None:
        """One quarter frame; returns the time (s) when a full one is in."""
        piece, value = (data >> 4) & 7, data & 0x0F
        self.pieces[piece] = value
        self.at = now
        if piece != 7 or any(p is None for p in self.pieces):
            return None
        p = self.pieces
        frames = p[0] | (p[1] << 4)
        secs = p[2] | (p[3] << 4)
        mins = p[4] | (p[5] << 4)
        hours = p[6] | ((p[7] & 1) << 4)
        self.fps = MTC_RATES[(p[7] >> 1) & 3]
        # the eight pieces take two frames to send: it is two frames later now
        self.seconds = hours * 3600 + mins * 60 + secs + (frames + 2) / self.fps
        return self.seconds

    def running(self, now: float) -> bool:
        return self.at is not None and now - self.at < 0.3

    def text(self) -> str:
        if self.seconds is None:
            return "--:--:--:--"
        t = self.seconds
        return f"{int(t // 3600):02d}:{int(t // 60 % 60):02d}:{int(t % 60):02d}:{int((t % 1) * self.fps):02d}"


def build_mtc(seconds: float, fps: float = 25.0) -> list[int]:
    """The eight quarter-frame data bytes for a time (tests, a virtual source)."""
    rate = {24.0: 0, 25.0: 1, 29.97: 2, 30.0: 3}[fps]
    fr = int(round((seconds % 1) * fps))
    s, m, h = int(seconds % 60), int(seconds // 60 % 60), int(seconds // 3600)
    vals = [fr & 15, fr >> 4, s & 15, s >> 4, m & 15, m >> 4, h & 15, (h >> 4) | (rate << 1)]
    return [(i << 4) | v for i, v in enumerate(vals)]
