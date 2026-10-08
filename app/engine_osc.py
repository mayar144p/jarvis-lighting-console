"""OSC in (app/osc.py): TouchOSC, Bitfocus Companion, QLab ... play the
show.  Off until switched on; then it listens on a UDP port for these
addresses (a value of 0..1 or 0..100 is a level):

    /jarvis/go [playback]        /jarvis/back [playback]
    /jarvis/cue <playback> <cue> /jarvis/release [playback]
    /jarvis/master <level>       /jarvis/blackout <0|1>
    /jarvis/playback/<n>/level <level>
    /jarvis/group/<n>/master <level>
    /jarvis/button/<id> <1 down|0 up>   (id like q1-3)
    /jarvis/macro <name or id>   /jarvis/cmd <command line>
    /jarvis/tap                  /jarvis/bpm <bpm>
    /jarvis/highlight <0|1>      /jarvis/autopilot <0|1>

Only show control: nothing here patches, deletes or saves.  Each message
is answered to its sender with /jarvis/ok or /jarvis/error and a line.

Part of the Engine class (see app/engine.py): a mixin.
"""
from __future__ import annotations

import re
import socket
import threading
import time

from app import osc as osc_mod
from app.engine_base import _truthy

OSC_PORT = 8000


def _level(v) -> int:
    x = float(v)
    return int(round(x * 100 if x <= 1.0 else x))


# OSC out: what the desk tells another program (QLab, Resolume, a video
# server, Companion) when feedback is on - one message per event
FEEDBACK = {
    "cue_go": "/jarvis/go", "cue_back": "/jarvis/back", "playback_release": "/jarvis/release",
    "master": "/jarvis/master", "blackout": "/jarvis/blackout", "timeline_play": "/jarvis/timeline/play",
    "timeline_stop": "/jarvis/timeline/stop", "quick_press": "/jarvis/button",
}
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


class OscMixin:
    # -- OSC out ----------------------------------------------------------------
    def osc_out_public(self) -> dict:
        st = self.__dict__.get("_osc_out") or {}
        return {"host": st.get("host", ""), "port": st.get("port", 53000), "feedback": bool(st.get("feedback")),
                "sent": st.get("sent", 0), "error": st.get("error")}

    def _a_osc_out(self, host=None, port=None, feedback=None, **_):
        """Where OSC goes out (QLab, Resolume, a video server...), and whether
        the desk tells it what it does (GO, master, blackout, buttons)."""
        st = self.__dict__.setdefault("_osc_out", {"port": 53000, "sent": 0})
        if host is not None:
            h = str(host).strip()
            if h and not _HOST.match(h):
                raise ValueError("an OSC target is an IP address or a computer's name")
            st["host"] = h
        if port is not None:
            p = int(port)
            if not 1 <= p <= 65535:
                raise ValueError("a port is 1-65535")
            st["port"] = p
        if feedback is not None:
            st["feedback"] = _truthy(feedback)
        st["error"] = None
        o = self.osc_out_public()
        return {"osc_out": o, "summary": (f"OSC out to {o['host']}:{o['port']}" + (", with feedback" if o["feedback"] else ""))
                if o["host"] else "OSC out off"}

    def _a_osc_send(self, address="", value=None, host=None, port=None, **_):
        """Send one OSC message (a cue can: start QLab's next cue, a Resolume
        clip).  To the OSC out target, or the host / port given."""
        addr = str(address or "").strip()
        if not addr.startswith("/") or len(addr) > 200 or any(c in addr for c in " #*,?[]{}"):
            raise ValueError("an OSC address starts with / (e.g. /go or /composition/layers/1/clips/2/connect)")
        st = self.__dict__.setdefault("_osc_out", {"port": 53000, "sent": 0})
        to_host = str(host).strip() if host else st.get("host", "")
        to_port = int(port) if port is not None else int(st.get("port", 53000))
        if not to_host or not _HOST.match(to_host):
            raise ValueError("set where OSC goes first (Settings -> MIDI & OSC -> OSC out)")
        if not 1 <= to_port <= 65535:
            raise ValueError("a port is 1-65535")
        args = [] if value in (None, "") else [value]
        self._osc_out_send(to_host, to_port, addr, *args)
        return {"summary": f"OSC {addr}" + (f" {value}" if args else "") + f" to {to_host}:{to_port}"}

    def _osc_out_send(self, host: str, port: int, addr: str, *args) -> None:
        st = self.__dict__.setdefault("_osc_out", {"port": 53000, "sent": 0})
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(osc_mod.build(addr, *args), (host, int(port)))
            st["sent"] = st.get("sent", 0) + 1
            st["error"] = None
        except OSError as exc:
            st["error"] = f"can't send to {host}:{port}: {exc}"[:200]
            raise ValueError(st["error"]) from None

    def _osc_out_saved(self) -> dict:
        st = self.__dict__.get("_osc_out") or {}
        return {k: st[k] for k in ("host", "port", "feedback") if k in st}

    def _osc_out_load(self, raw) -> None:
        """A show's OSC out (saved with it: each venue has its own video server)."""
        if not isinstance(raw, dict):
            return
        try:
            self._a_osc_out(host=raw.get("host", ""), port=raw.get("port", 53000), feedback=raw.get("feedback", False))
        except (ValueError, TypeError):
            pass

    def _osc_feedback(self, action: str, params: dict, res: dict) -> None:
        """After an action: tell the OSC out target, when feedback is on.
        Never raises - a missing video server must not stop a GO."""
        st = self.__dict__.get("_osc_out") or {}
        addr = FEEDBACK.get(action)
        if not addr or not st.get("feedback") or not st.get("host"):
            return
        if action in ("cue_go", "cue_back"):
            args = [int(res.get("playback") or params.get("playback") or 1), int(res.get("cue") or 0)]
        elif action == "playback_release":
            args = [int(params.get("playback") or 1)]
        elif action == "master":
            args = [float(self.master) / 100.0]
        elif action == "blackout":
            args = [1 if self.blackout else 0]
        elif action == "quick_press":
            args = [str(params.get("id") or ""), 1]
        else:
            args = []
        try:
            self._osc_out_send(st["host"], st.get("port", 53000), addr, *args)
        except ValueError:
            pass
    def osc_public(self) -> dict:
        st = self.__dict__.get("_osc") or {}
        return {"on": bool(st.get("thread")), "port": st.get("port", OSC_PORT), "count": st.get("count", 0),
                "last": st.get("last"), "error": st.get("error")}

    def _a_osc(self, state=None, port=None, **_):
        """Listen for OSC (TouchOSC, Companion ...) on a UDP port."""
        st = self.__dict__.setdefault("_osc", {"count": 0})
        want = (not st.get("thread")) if state is None else _truthy(state)
        if port is not None:
            p = int(port)
            if not 1024 <= p <= 65535:
                raise ValueError("an OSC port is 1024-65535")
            if st.get("thread") and p != st.get("port"):
                self._osc_stop()
            st["port"] = p
        if want:
            err = self._osc_start(st.get("port", OSC_PORT))
            if err:
                raise ValueError(err)
            return {"osc": self.osc_public(), "summary": f"OSC on UDP {st['port']}: anyone on this network can play the show"}
        self._osc_stop()
        return {"osc": self.osc_public(), "summary": "OSC off"}

    def _osc_start(self, port: int) -> str | None:
        st = self._osc
        if st.get("thread"):
            return None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", int(port)))
            sock.settimeout(0.3)
        except OSError as exc:
            st["error"] = f"can't listen on UDP {port}: {exc}"
            return st["error"]
        st.update({"port": int(port), "error": None, "sock": sock})
        stop = st["stop"] = threading.Event()

        def loop():
            while not stop.is_set():
                try:
                    pkt, peer = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                # one bad packet (no terminator, a missing argument) is
                # skipped and counted - it must never stop the listener
                try:
                    msgs = osc_mod.parse(pkt)
                except Exception:
                    st["bad"] = st.get("bad", 0) + 1
                    continue
                for addr, args in msgs:
                    try:
                        ok, said = self.osc_handle(addr, args)
                    except Exception as exc:
                        ok, said = False, f"{addr}: {exc}"
                    try:
                        sock.sendto(osc_mod.build("/jarvis/ok" if ok else "/jarvis/error", said), peer)
                    except OSError:
                        pass
            sock.close()

        st["thread"] = threading.Thread(target=loop, name="jarvis-osc", daemon=True)
        st["thread"].start()
        return None

    def _osc_stop(self) -> None:
        st = self.__dict__.get("_osc") or {}
        if st.get("stop"):
            st["stop"].set()
        th = st.get("thread")
        if th and th is not threading.current_thread():
            th.join(1.0)
        st["thread"] = None

    def osc_handle(self, addr: str, args: list) -> tuple[bool, str]:
        """One OSC message -> one action.  (ok, what happened)."""
        st = self.__dict__.setdefault("_osc", {"count": 0})
        st["count"] = st.get("count", 0) + 1
        st["last"] = {"address": addr, "args": [a if isinstance(a, (int, float, str)) else str(a) for a in args][:4],
                      "at": time.time()}
        parts = [p for p in addr.split("/") if p]
        if not parts or parts[0] != "jarvis":
            return False, f"not a Jarvis address: {addr}"
        cmd, rest = (parts[1] if len(parts) > 1 else ""), parts[2:]
        a0 = args[0] if args else None
        try:
            if cmd in ("go", "back", "release"):
                pb = int(a0 if a0 is not None else (rest[0] if rest else 1))
                action = {"go": "cue_go", "back": "cue_back", "release": "playback_release"}[cmd]
                r = self.act(action, playback=pb)
            elif cmd == "cue":
                r = self.act("cue_go", playback=int(args[0]), cue=int(args[1]))
            elif cmd == "master":
                r = self.act("master", level=_level(a0))
            elif cmd == "blackout":
                r = self.act("blackout", state=1 if (a0 is None or _truthy(a0)) else 0)
            elif cmd == "playback" and len(rest) >= 2 and rest[1] == "level":
                r = self.act("playback_level", playback=int(rest[0]), level=_level(a0))
            elif cmd == "group" and len(rest) >= 2 and rest[1] == "master":
                r = self.act("group_master", group=int(rest[0]), level=_level(a0))
            elif cmd == "button" and rest:
                bid = rest[0] if len(rest) == 1 else f"q{int(rest[0])}-{int(rest[1])}"
                r = self.act("quick_press", id=bid, down=True if a0 is None else bool(float(a0)))
            elif cmd == "macro":
                r = self.act("macro_run", id=str(a0 if a0 is not None else (rest[0] if rest else "")))
            elif cmd == "cmd":
                r = self.act("run_command", text=str(a0 or ""))
            elif cmd == "tap":
                if a0 is not None and not _truthy(a0):
                    return True, "tap released"             # a button's release
                r = self.act("tempo_tap")
            elif cmd == "bpm":
                r = self.act("tempo_set", bpm=float(a0))
            elif cmd == "highlight":
                r = self.act("highlight", state=a0 is None or _truthy(a0))
            elif cmd == "autopilot":
                r = self.act("autopilot", state=a0 is None or _truthy(a0))
            else:
                return False, f"unknown address {addr}"
        except (TypeError, ValueError, IndexError) as exc:
            return False, f"{addr}: {exc}"
        return bool(r.get("ok")), str(r.get("summary") or r.get("error") or "")
