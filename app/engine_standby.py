"""A backup desk (grandMA3's "tracking backup"): a second computer follows
the main desk live and takes over the lights if the main one stops.

    The main desk     answers GET /api/console/standby_feed (its pairing
                      code, like any remote): the show - only when it
                      changed - and what is live now: each playback's cue
                      and fader, the master, blackout, the programmer, the
                      timeline and the buttons that are on.
    The backup desk   Settings -> Backup desk: the main's address and
                      pairing code.  It copies the show and the live state
                      twice a second, with its own output OFF (two desks
                      sending to the same lights flicker).  Cue actions
                      (OSC, macros) are not run a second time.
    Taking over       when the main hasn't answered for `after` seconds
                      (or on "Take over now"): output on, the timeline and
                      the buttons carry on where the main was, and every
                      screen says so.  If the main comes back, the backup
                      warns - it never hands back by itself.

The pairing code is kept in DATA/standby.json on the backup computer only:
never in a show, never on screen.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request

from app.engine_base import _truthy

POLL_S = 0.5
TIMEOUT_S = 1.5


def _host_ok(host: str) -> str:
    host = str(host or "").strip().removeprefix("http://").rstrip("/")
    if not host or len(host) > 100 or not all(c.isalnum() or c in ".-:[]" for c in host):
        raise ValueError("the main desk's address is like 192.168.1.20:8787 (Show it: Settings -> Other computers on the main desk)")
    return host


class StandbyMixin:
    # -- the main desk's side --------------------------------------------------
    def standby_feed(self, have: str = "") -> dict:
        """The show (when it isn't the one the backup has) and the live state."""
        with self.lock:
            cache = self.__dict__.get("_standby_cache")
            if not cache or cache[0] != self.act_rev:
                text = self._autosave_payload()
                cache = self._standby_cache = (self.act_rev, hashlib.sha1(text.encode()).hexdigest()[:16], text)
            live = {
                "playbacks": [{"n": pb["n"], "index": pb["index"], "active": bool(pb["active"]),
                               "level": pb.get("level", 100)} for pb in self.playbacks],
                "master": self.master, "blackout": bool(self.blackout),
                "programmer": {str(k): dict(v) for k, v in self.programmer.items()},
                "timeline": {"playing": bool(self.tl["playing"]), "pos": round(self._tl_now(), 3)},
                "buttons": sorted(k for k in self.quick_active
                                  if (next((b for b in self.quick if b["id"] == k), {}) or {}).get("mode") == "toggle"),
                "output": bool(self.live)}
            out = {"show_hash": cache[1], "show_file": self.show_file or "", "live": live, "at": time.time()}
            if have != cache[1]:
                out["show"] = json.loads(cache[2])
            return out

    # -- the backup desk's side ------------------------------------------------
    def _standby(self) -> dict:
        st = self.__dict__.get("_standby_st")
        if st is None:
            st = self._standby_st = {"following": False, "host": "", "after": 3.0, "auto": True,
                                     "last_ok": None, "error": None, "took_over": None, "main_back": False,
                                     "show_hash": None, "show_file": "", "live": None, "stop": None}
            try:
                raw = json.loads((self.show_dir.parent / "standby.json").read_text(encoding="utf-8"))
                st["host"] = str(raw.get("host") or "")
                st["after"] = float(raw.get("after") or 3.0)
                st["auto"] = bool(raw.get("auto", True))
                self._standby_code = str(raw.get("code") or "")
            except (OSError, ValueError, TypeError, AttributeError):
                self._standby_code = ""
        return st

    def standby_public(self) -> dict:
        st = self.__dict__.get("_standby_st")
        if not st:
            return {"following": False, "host": "", "took_over": None}
        age = None if st["last_ok"] is None else round(time.monotonic() - st["last_ok"], 1)
        return {"following": st["following"], "host": st["host"], "after": st["after"], "auto": st["auto"],
                "seen_s": age, "error": st["error"], "took_over": st["took_over"], "main_back": st["main_back"],
                "show_file": st["show_file"], "has_code": bool(self.__dict__.get("_standby_code"))}

    def _standby_save_cfg(self) -> None:
        st = self._standby()
        try:
            p = self.show_dir.parent / "standby.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"host": st["host"], "code": self.__dict__.get("_standby_code", ""),
                                     "after": st["after"], "auto": st["auto"]}), encoding="utf-8")
        except OSError:
            pass

    def _standby_fetch(self, host: str, code: str, have: str) -> dict:
        req = urllib.request.Request(f"http://{host}/api/console/standby_feed?have={have}",
                                     headers={"X-Jarvis-Token": code} if code else {})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ValueError("the main desk refused the pairing code" if exc.code in (401, 403)
                             else f"the main desk answered {exc.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ValueError(f"no answer from the main desk at {host} ({getattr(exc, 'reason', exc)})") from None

    def _a_standby(self, host=None, code=None, after=None, auto=None, **_):
        """Follow a main desk as its backup: its address, pairing code, how
        many seconds of silence before taking over, and whether to take over
        by itself (auto) or only on "Take over now"."""
        st = self._standby()
        h = _host_ok(host if host is not None else st["host"])
        c = str(code).strip().replace("-", "") if code else self.__dict__.get("_standby_code", "")
        if after is not None:
            st["after"] = max(1.0, min(30.0, float(after)))
        if auto is not None:
            st["auto"] = _truthy(auto)
        # unlocked: the network (the address and code work, or say why)
        first = self._standby_fetch(h, c, "")
        if not first.get("show"):
            raise ValueError("the main desk sent no show")
        with self.lock:
            self._standby_stop_thread()
            st.update({"host": h, "error": None, "took_over": None, "main_back": False, "show_hash": None})
            self._standby_code = c
            self._standby_save_cfg()
            if self.live:
                self._a_set_output(state=False)           # one desk sends at a time
            self._standby_apply(first)
            st["following"] = True
            st["last_ok"] = time.monotonic()
            stop = threading.Event()
            st["stop"] = stop
        threading.Thread(target=self._standby_loop, args=(stop,), name="jarvis-standby", daemon=True).start()
        return {"standby": self.standby_public(),
                "summary": f"following the main desk at {h}: this desk's output is off until it takes over"}

    def _a_standby_take(self, **_):
        """Take over now: this desk's output on, carrying on where the main was."""
        st = self._standby()
        if not st["following"]:
            raise ValueError("this desk isn't following a main desk")
        with self.lock:
            self._standby_takeover("by hand")
        return {"standby": self.standby_public(), "summary": "this desk has taken over the lights"}

    def _a_standby_stop(self, **_):
        """Stop following (the output stays as it is)."""
        st = self._standby()
        self._standby_stop_thread()
        st["following"] = False
        return {"standby": self.standby_public(), "summary": "no longer following the main desk"}

    def _standby_stop_thread(self) -> None:
        st = self.__dict__.get("_standby_st") or {}
        ev = st.get("stop")
        if ev:
            ev.set()
        st["stop"] = None

    def _standby_loop(self, stop: threading.Event) -> None:
        st = self._standby()
        while not stop.wait(POLL_S):
            try:
                feed = self._standby_fetch(st["host"], self.__dict__.get("_standby_code", ""), st["show_hash"] or "")
            except ValueError as exc:
                with self.lock:
                    st["error"] = str(exc)
                    if st["following"] and st["auto"] and st["last_ok"] is not None \
                            and time.monotonic() - st["last_ok"] >= st["after"]:
                        self._standby_takeover(f"the main desk went quiet for {st['after']:g} s")
                continue
            with self.lock:
                if stop.is_set():
                    return
                st["last_ok"] = time.monotonic()
                st["error"] = None
                if st["took_over"]:
                    # the main is back while this desk sends: say so, never hand back alone
                    st["main_back"] = True
                    continue
                try:
                    self._standby_apply(feed)
                except (ValueError, KeyError, TypeError) as exc:
                    st["error"] = f"couldn't copy the main desk's show: {exc}"[:200]

    def _standby_apply(self, feed: dict) -> None:
        """The main desk's show (if new) and live state onto this desk."""
        with self.lock:
            st = self._standby()
            if feed.get("show") is not None and feed.get("show_hash") != st["show_hash"]:
                self._apply_show_payload(feed["show"], feed.get("show_file") or "backup")
                st["show_hash"] = feed.get("show_hash")
                st["show_file"] = feed.get("show_file") or ""
                self.act_rev += 1
            live = feed.get("live") or {}
            st["live"] = live
            now = self._clock()
            before = (self.master, self.blackout, [(pb["index"], pb["active"], pb.get("level")) for pb in self.playbacks])
            for row in live.get("playbacks") or []:
                try:
                    pb = self._playback(int(row["n"]))
                except (ValueError, KeyError, TypeError):
                    continue
                pb["level"] = row.get("level", pb.get("level", 100))
                idx = int(row.get("index", -1))
                if row.get("active") and pb["stack"] and 0 <= idx < len(pb["stack"]):
                    if not pb["active"] or pb["index"] != idx:
                        self._standby_goto(pb, idx, now)
                elif pb["active"]:
                    self._a_playback_release(playback=pb["n"])
            self.master = live.get("master", self.master)
            self.blackout = bool(live.get("blackout", self.blackout))
            patched = {h["head_no"] for h in self.patch}
            prog = {}
            for k, row in (live.get("programmer") or {}).items():
                try:
                    n = int(k)
                except (TypeError, ValueError):
                    continue
                if n in patched and isinstance(row, dict):
                    prog[n] = {str(r): v for r, v in row.items()}
            if prog != self.programmer:
                self.programmer = prog
                self.act_rev += 1
            if before != (self.master, self.blackout, [(pb["index"], pb["active"], pb.get("level")) for pb in self.playbacks]):
                self.act_rev += 1                  # this desk's screens show the change

    def _standby_goto(self, pb: dict, index: int, now: float) -> None:
        """A cue as the main desk took it, minus its actions (the main ran
        those: QLab must not get the same GO twice)."""
        stack = pb["stack"]
        was = self._pb_values(pb, now) if pb["index"] >= 0 else {}
        cue = stack[index]
        target = self._scaled_cue({"values": self._tracked(pb, index)}, None)
        pb["target_mib"] = self._mib(pb, index, target)
        pb["fade"] = {"t0": now, "dur": float(cue.get("fade_s") or 0.0), "from": was,
                      "parts": dict(cue.get("times") or {})}
        pb["target"] = target
        pb["at"] = None
        pb["index"] = index
        pb["active"] = True
        self._cue_fx_start(pb, cue)
        self._order += 1
        pb["order"] = self._order

    def _standby_takeover(self, why: str) -> None:
        """Called with the lock held: this desk drives the lights now."""
        st = self._standby()
        if st["took_over"]:
            return
        self._standby_stop_thread()
        st["following"] = False
        st["took_over"] = {"at": time.time(), "why": why}
        live = st.get("live") or {}
        now = self._clock()
        for pb in self.playbacks:
            if pb["active"]:
                self._arm_follow(pb, now)         # auto-follow cues carry on from here
        tl = live.get("timeline") or {}
        if tl.get("playing"):
            # where the main's timeline is now: where it was, plus the time since
            seen = st["last_ok"] or time.monotonic()
            self._a_timeline_play(at=float(tl.get("pos") or 0) + (time.monotonic() - seen))
        for bid in live.get("buttons") or []:
            if bid not in self.quick_active:
                try:
                    self._a_quick_press(id=bid, down=True, immediate=True)
                except (ValueError, KeyError, TypeError):
                    pass
        try:
            self._a_set_output(state=True, confirm=True)
        except ValueError as exc:
            st["error"] = f"couldn't start the output: {exc}"[:200]
        self.boot_notice = {
            "id": "standby_took_over", "title": "This desk has taken over the lights",
            "text": f"The backup desk took over ({why}). It carries on from the main desk's last cue, faders, "
                    "timeline and buttons. If the main desk comes back, only one of them should send: "
                    "stop the other one's output."}
        self.act_rev += 1
