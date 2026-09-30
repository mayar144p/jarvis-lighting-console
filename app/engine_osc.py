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

import socket
import threading
import time

from app import osc as osc_mod
from app.engine_base import _truthy

OSC_PORT = 8000


def _level(v) -> int:
    x = float(v)
    return int(round(x * 100 if x <= 1.0 else x))


class OscMixin:
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
                for addr, args in osc_mod.parse(pkt):
                    ok, said = self.osc_handle(addr, args)
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
