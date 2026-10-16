"""A light show per song (SoundSwitch's idea): a song keeps its own
timeline and the cues it plays, so the same track looks the same every
night - and can be changed.

    Keep as a song   the timeline as it is (music, markers, tracks) and the
                     cue lists its cue tracks play, under the song's name.
    Play a song      its timeline comes back, its cues go back on their
                     playbacks, and it plays.
    The decks        with Pro DJ Link, a song can be tied to the track on a
                     CDJ (its rekordbox id: the same USB stick, the same id
                     every night).  "Songs follow the decks": when the tempo
                     master plays a tied track, its song starts, in step
                     with the deck (its beat number), at the deck's speed;
                     it pauses with the deck.

Saved with the show (self.songs); the deck state is never saved.
"""
from __future__ import annotations

import copy
import re
import time

from app import timeline as tl_mod
from app.engine_base import _truthy

MAX_SONGS = 200
DRIFT_S = 0.25          # re-seek a following song when it is this far off the deck
XFADE_S = 2.0           # one song's look into the next, or into the autopilot


def _clean_name(name, fallback: str) -> str:
    n = re.sub(r"\s+", " ", str(name or "")).strip()[:60]
    return n or fallback


class SongsMixin:
    def _songs(self) -> list[dict]:
        return self.__dict__.setdefault("songs", [])

    def _song(self, id) -> dict:
        s = next((x for x in self._songs() if x["id"] == str(id)), None)
        if s is None:
            raise ValueError(f"no song {id!r}")
        return s

    def songs_public(self) -> dict:
        st = self.__dict__.get("_song_state") or {}
        return {"songs": [{"id": s["id"], "name": s["name"], "length": s["timeline"]["length"],
                           "bpm": s["timeline"]["bpm"], "parts": len(s["timeline"].get("markers") or []),
                           "music": (s["timeline"].get("audio") or {}).get("name"),
                           "deck": bool(s.get("deck")), "track": (s.get("deck") or {}).get("track")}
                          for s in self._songs()],
                "current": self.__dict__.get("song_current"),
                "follow": bool(self.__dict__.get("song_follow")),
                "following": st.get("song"), "deck": st.get("deck_track"),
                "between": self.__dict__.get("song_between", "autopilot"),
                "fallback": bool(st.get("auto")), "fallback_problem": self._song_fallback_problem()}

    # -- keep / play ---------------------------------------------------------
    def _a_song_save(self, name=None, id=None, **_):
        """Keep the timeline (and the cues it plays) as a song; with id,
        update that song."""
        doc = copy.deepcopy(self.timeline)
        if not doc.get("tracks"):
            raise ValueError("the timeline is empty: build its show first (Auto show, or add tracks)")
        cues = {}
        for tr in doc["tracks"]:
            if tr.get("kind") == "cue":
                pb = self._playback(tr.get("playback") or 1)
                cues[str(pb["n"])] = copy.deepcopy(pb["stack"])
        songs = self._songs()
        music = (doc.get("audio") or {}).get("name") or ""
        fallback = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", music) or f"Song {len(songs) + 1}"
        if id:
            s = self._song(id)
            s.update({"timeline": doc, "cues": cues})
            if name:
                s["name"] = _clean_name(name, s["name"])
            verb = "updated"
        else:
            if len(songs) >= MAX_SONGS:
                raise ValueError(f"{MAX_SONGS} songs is the most a show keeps")
            n = 1 + max((int(x["id"][1:]) for x in songs if x["id"][1:].isdigit()), default=0)
            s = {"id": f"s{n}", "name": _clean_name(name, fallback), "timeline": doc, "cues": cues, "deck": None}
            songs.append(s)
            verb = "kept"
        self.song_current = s["id"]
        return {"song": s["id"], "songs": self.songs_public(),
                "summary": f"song \"{s['name']}\" {verb} ({len(doc['tracks'])} track(s), "
                           f"{sum(len(v) for v in cues.values())} cue(s))"}

    def _song_apply(self, s: dict) -> None:
        """The song's timeline and cues back on the desk (stopped)."""
        if self.tl["playing"]:
            self._a_timeline_pause()
        for pbn, stack in s["cues"].items():
            try:
                pb = self._playback(int(pbn))
            except (ValueError, TypeError):
                continue
            if pb["active"]:
                self._a_playback_release(playback=pb["n"])
            pb["stack"] = copy.deepcopy(stack)
            pb["index"] = -1
        self._tl_set_doc(copy.deepcopy(s["timeline"]))
        self.tl["pos"] = 0.0
        self.song_current = s["id"]

    def _a_song_load(self, id=None, play=False, at=None, **_):
        """Put a song on the desk: its timeline, its cues; play=True starts it."""
        s = self._song(id)
        self._song_apply(s)
        self._song_rate(1.0)
        # started by hand: following the decks, the master deck's track
        # decides again at its next report (the deck wins)
        st = self.__dict__.get("_song_state")
        if st:
            st["song"] = None
            self._song_fallback(False)
        if _truthy(play):
            self._a_timeline_play(at=float(at) if at is not None else 0.0)
        return {"song": s["id"], "songs": self.songs_public(), "transport": self._tl_transport(),
                "timeline": self._timeline_public(),
                "summary": f"song \"{s['name']}\"" + (" playing" if _truthy(play) else " on the timeline")}

    def _a_song_rename(self, id=None, name=None, **_):
        s = self._song(id)
        s["name"] = _clean_name(name, s["name"])
        return {"songs": self.songs_public(), "summary": f"song renamed \"{s['name']}\""}

    def _a_song_delete(self, id=None, **_):
        s = self._song(id)
        self._songs().remove(s)
        if self.__dict__.get("song_current") == s["id"]:
            self.song_current = None
        return {"songs": self.songs_public(), "summary": f"song \"{s['name']}\" deleted"}

    # -- the decks -----------------------------------------------------------
    def _a_song_link(self, id=None, track=None, first_beat=None, unlink=False, **_):
        """Tie a song to a track on the CDJs: the one the tempo master is
        playing now (or `track`, a rekordbox id).  first_beat: where beat 1
        of the deck's beat grid is in the song's music, in seconds."""
        s = self._song(id)
        if _truthy(unlink):
            s["deck"] = None
            return {"songs": self.songs_public(), "summary": f"\"{s['name']}\" no longer tied to a track"}
        if track is None:
            now = (self.__dict__.get("_song_state") or {}).get("deck_track")
            if not now:
                raise ValueError("no track playing on the master deck (Pro DJ Link): play it, then tie it")
            track = now
        t = int(track)
        if not 0 < t < 1 << 32:
            raise ValueError("a track is a rekordbox id (a whole number)")
        for other in self._songs():
            if other is not s and (other.get("deck") or {}).get("track") == t:
                other["deck"] = None            # one song per track
        fb = float(first_beat) if first_beat is not None else float((s.get("deck") or {}).get("first_beat", 0.0))
        s["deck"] = {"track": t, "first_beat": round(max(0.0, min(fb, 60.0)), 3)}
        return {"songs": self.songs_public(), "summary": f"\"{s['name']}\" plays when track {t} is on the master deck"}

    def _a_song_follow(self, state=True, between=None, **_):
        """Songs follow the decks (Pro DJ Link) on / off.  between: what the
        lights do while the deck plays a track with no song - "autopilot"
        (its looks, picked by the music) or "hold" (the last look stays)."""
        if between is not None:
            if between not in ("autopilot", "hold"):
                raise ValueError("between songs: autopilot or hold")
            self.song_between = between
        if state is None:
            return {"songs": self.songs_public(), "summary": f"between songs: {self.__dict__.get('song_between', 'autopilot')}"}
        on = _truthy(state)
        self.song_follow = on
        err = None
        if on:
            err = self._prodj_start()
        else:
            st = self.__dict__.get("_song_state") or {}
            if st.get("song") and self.tl["playing"]:
                self._a_timeline_pause()
            self._song_fallback(False)
            self._song_state = {}
            self._song_rate(1.0)
        if err:
            self.song_follow = False
            raise ValueError(err)
        return {"songs": self.songs_public(),
                "summary": "songs follow the decks" if on else "songs no longer follow the decks"}

    def _song_rate(self, rate: float) -> None:
        """The timeline's speed (a deck pitched +4% plays its song 4% faster)."""
        st = self.tl
        rate = max(0.5, min(2.0, float(rate)))
        if abs(st.get("rate", 1.0) - rate) < 1e-4:
            return
        if st["playing"]:
            now = self._clock()
            st["pos0"] = self._tl_now()
            st["t0"] = now
        st["rate"] = rate

    def _song_pbs(self, song: dict) -> list[dict]:
        out = []
        for n in song.get("cues") or {}:
            try:
                out.append(self._playback(int(n)))
            except (ValueError, TypeError):
                continue
        return out

    def _song_fallback_problem(self) -> str | None:
        """Why the autopilot can't fill in between songs, or None."""
        if self.__dict__.get("song_between", "autopilot") != "autopilot" or not self._songs():
            return None
        ap = self._ap()
        song_pbs = {int(n) for s in self._songs() for n in (s.get("cues") or {}) if str(n).isdigit()}
        if ap["playback"] in song_pbs:
            return (f"the autopilot runs playback {ap['playback']}, which the songs use: "
                    "give it a playback of its own (its looks) in the autopilot's settings")
        if len(self.playbacks[ap["playback"] - 1]["stack"]) < 2:
            return f"the autopilot's playback {ap['playback']} needs at least 2 looks (cues)"
        return None

    def _song_fallback(self, on: bool) -> None:
        """Between songs, the autopilot's looks (faded in); off again, faded out."""
        st = self.__dict__.setdefault("_song_state", {})
        ap = self._ap()
        if on:
            if st.get("auto") or ap["on"] or self._song_fallback_problem() \
                    or self.__dict__.get("song_between", "autopilot") != "autopilot":
                return
            pb = self._playback(ap["playback"])
            self._a_playback_level(playback=pb["n"], level=0)
            self._a_autopilot(state=True)
            if not pb["active"]:
                self._goto(pb, 0, self._clock())        # a look at once, not at the next pick
            self._a_playback_level(playback=pb["n"], level=100, xfade=XFADE_S)
            st["auto"] = True
        elif st.get("auto"):
            self._a_autopilot(state=False)
            self._a_playback_level(playback=ap["playback"], level=0, xfade=XFADE_S)
            st["auto"] = False

    def songs_dj_status(self, status: dict) -> None:
        """A CDJ's status (called with the lock held): the tempo master's
        track, beat and speed drive the song tied to that track.  A new song
        fades in from the look before it; a track with no song hands over
        to the autopilot (or holds the last look)."""
        st = self.__dict__.setdefault("_song_state", {})
        master = self._decks().master(time.monotonic())
        if status.get("device") != master:
            return
        track = status.get("track") or None
        st["deck_track"] = track
        if not self.__dict__.get("song_follow"):
            return
        song = next((s for s in self._songs() if track and (s.get("deck") or {}).get("track") == track), None)
        if song is None:
            if st.get("song"):
                # the DJ moved on to a track with no song
                if self.tl["playing"]:
                    self._a_timeline_pause()
                old = next((s for s in self._songs() if s["id"] == st["song"]), None)
                if old and self.__dict__.get("song_between", "autopilot") == "autopilot" \
                        and not self._song_fallback_problem():
                    for pb in self._song_pbs(old):
                        self._a_playback_level(playback=pb["n"], level=0, xfade=XFADE_S)
                st["song"] = None
            if status.get("playing"):
                self._song_fallback(True)
            return
        bpm = float(song["timeline"].get("bpm") or 120.0)
        first = float(song["deck"].get("first_beat") or 0.0)
        beat = status.get("beat")
        want = first + (beat - 1) * 60.0 / bpm if beat and beat > 0 else None
        if st.get("song") != song["id"]:
            now = self._clock()
            # what is on stage now, so the new song's look fades in from it
            before = {pb["n"]: (self._pb_values(pb, now) if pb["active"] and pb["index"] >= 0 else {},
                                self._pb_level_now(pb, now)) for pb in self._song_pbs(song)}
            self._song_fallback(False)
            self._song_apply(song)
            st["song"] = song["id"]
            st["beat"] = None
            if status.get("playing"):
                self._song_rate(status.get("speed") or 1.0)
                self._a_timeline_play(at=want or 0.0)
            for pb in self._song_pbs(song):
                was, lvl = before.get(pb["n"], ({}, 100))
                if pb["active"] and pb.get("fade") is not None:
                    pb["fade"]["from"] = was
                    pb["fade"]["dur"] = max(float(pb["fade"].get("dur") or 0.0), XFADE_S)
                    pb["fade"]["parts"] = {}
                if pb.get("level", 100) < 100:          # faded out (or fading out) between songs
                    pb["level"], pb["xfade"] = lvl, None    # from where it is now, back up
                    self._a_playback_level(playback=pb["n"], level=100, xfade=XFADE_S)
            return
        self._song_rate(status.get("speed") or 1.0)
        if not status.get("playing"):
            if self.tl["playing"]:
                self._a_timeline_pause()
            return
        if not self.tl["playing"]:
            self._a_timeline_play(at=want if want is not None else None)
            return
        if want is not None and beat != st.get("beat"):
            st["beat"] = beat                    # a new beat started just now
            if abs(self._tl_now() - want) > DRIFT_S:
                self._a_timeline_seek(t=want, chase=False)

    # -- the show ------------------------------------------------------------
    def _songs_saved(self) -> list[dict]:
        return copy.deepcopy(self._songs())

    def _songs_load(self, raw) -> None:
        out, seen = [], set()
        for s in raw if isinstance(raw, list) else []:
            if not isinstance(s, dict) or not isinstance(s.get("timeline"), dict):
                continue
            sid = str(s.get("id") or "")
            if not re.fullmatch(r"s\d{1,6}", sid) or sid in seen:
                continue
            seen.add(sid)
            cues = {str(k): v for k, v in (s.get("cues") or {}).items()
                    if str(k).isdigit() and isinstance(v, list)}
            deck = s.get("deck") if isinstance(s.get("deck"), dict) else None
            if deck:
                try:
                    deck = {"track": int(deck["track"]), "first_beat": float(deck.get("first_beat") or 0.0)}
                except (KeyError, TypeError, ValueError):
                    deck = None
            out.append({"id": sid, "name": _clean_name(s.get("name"), sid),
                        "timeline": tl_mod.normalise(s["timeline"]), "cues": cues, "deck": deck})
            if len(out) >= MAX_SONGS:
                break
        self.songs = out
        self.song_current = None
        self._song_state = {}
