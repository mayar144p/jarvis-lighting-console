"""Philips Hue lamps follow the desk: a party room or a small venue with
smart bulbs needs no DMX at all.

The Hue bridge's own local API (plain HTTP on the home network, no cloud):
find the bridge, press its round button and pair (the bridge hands out a
key), then each linked desk light's colour and brightness are sent to its
Hue lamp.  A bridge takes about ten lamp changes a second, so only the
changes go, newest first, at most RATE a second: Hue lamps follow a slow
fade nicely; a chase or a strobe is beyond them (the Settings page says so).

The key is kept in DATA/hue.json on this computer only - never in a show,
never shown on screen.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

RATE = 10                 # lamp changes a second, the bridge's comfortable limit
TICK_S = 0.1
TIMEOUT_S = 2.0
DEVICE = "haze#desk"


class HueError(ValueError):
    pass


def rgb_to_xy(r: int, g: int, b: int) -> tuple[float, float]:
    """sRGB 0-255 -> CIE xy, as Philips' own colour notes do it (gamma, then
    the wide-gamut D65 matrix).  Black gives the white point."""
    def lin(c):
        c = max(0, min(255, c)) / 255.0
        return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92
    rl, gl, bl = lin(r), lin(g), lin(b)
    X = rl * 0.664511 + gl * 0.154324 + bl * 0.162028
    Y = rl * 0.283881 + gl * 0.668433 + bl * 0.047685
    Z = rl * 0.000088 + gl * 0.072310 + bl * 0.986039
    s = X + Y + Z
    if s <= 0:
        return 0.3127, 0.3290
    return round(X / s, 4), round(Y / s, 4)


def lamp_state(hexcol: str, a: float) -> dict:
    """A desk look (colour at full, intensity 0..1) -> a Hue lamp state."""
    if a <= 0.004:
        return {"on": False}
    h = (hexcol or "#ffffff").lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        r = g = b = 255
    x, y = rgb_to_xy(r, g, b)
    return {"on": True, "bri": max(1, min(254, round(a * 254))), "xy": [x, y]}


def _host_ok(host: str) -> str:
    host = str(host or "").strip()
    if not host or len(host) > 253 or not all(c.isalnum() or c in ".-:" for c in host):
        raise HueError("the bridge's address is an IP like 192.168.1.20 (the Hue app shows it)")
    return host


class Bridge:
    """The bridge's local API (v1), over plain HTTP."""

    def __init__(self, host: str, user: str = "") -> None:
        self.host = _host_ok(host)
        self.user = user

    def _call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://{self.host}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return json.loads(r.read().decode("utf-8") or "null")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise HueError(f"no Hue bridge answering at {self.host} ({getattr(exc, 'reason', exc)})") from None

    @staticmethod
    def _errors(reply) -> list[dict]:
        return [x["error"] for x in reply if isinstance(x, dict) and "error" in x] if isinstance(reply, list) else []

    def pair(self) -> str:
        reply = self._call("POST", "/api", {"devicetype": DEVICE})
        errs = self._errors(reply)
        if errs:
            if errs[0].get("type") == 101:
                raise HueError("press the round button on top of the bridge, then Pair again within 30 seconds")
            raise HueError(f"the bridge said: {errs[0].get('description', 'no')}")
        try:
            self.user = str(reply[0]["success"]["username"])
        except (KeyError, IndexError, TypeError):
            raise HueError("the bridge gave an answer Haze doesn't understand") from None
        return self.user

    def lights(self) -> list[dict]:
        reply = self._call("GET", f"/api/{self.user}/lights")
        errs = self._errors(reply)
        if errs:
            raise HueError("the bridge doesn't know this computer any more: pair again"
                           if errs[0].get("type") == 1 else f"the bridge said: {errs[0].get('description')}")
        if not isinstance(reply, dict):
            raise HueError("the bridge gave an answer Haze doesn't understand")
        out = []
        for lid, raw in reply.items():
            st = (raw or {}).get("state") or {}
            out.append({"id": str(lid), "name": str(raw.get("name") or f"Lamp {lid}")[:60],
                        "colour": "xy" in st, "dims": "bri" in st,
                        "reachable": bool(st.get("reachable", True)), "model": str(raw.get("modelid") or "")[:20]})
        return sorted(out, key=lambda x: (int(x["id"]) if x["id"].isdigit() else 1 << 30, x["id"]))

    def set(self, lamp: str, state: dict) -> None:
        reply = self._call("PUT", f"/api/{self.user}/lights/{lamp}/state", state)
        errs = self._errors(reply)
        if errs:
            raise HueError(f"lamp {lamp}: {errs[0].get('description', 'refused')}")


def discover(timeout: float = 2.0) -> list[dict]:
    """Hue bridges on this network, by mDNS (_hue._tcp).  [{host, name}]."""
    q = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)
    for part in ("_hue", "_tcp", "local"):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00" + struct.pack(">HH", 12, 1)            # PTR, IN
    found = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    except OSError:
        return []
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        s.settimeout(0.2)
        s.sendto(q, ("224.0.0.251", 5353))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, (ip, _port) = s.recvfrom(9000)
            except socket.timeout:
                continue
            if b"_hue" in data and ip not in found:
                found[ip] = {"host": ip, "name": "Hue bridge"}
    except OSError:
        pass
    finally:
        s.close()
    return list(found.values())


class HueLink:
    """The pairing (DATA/hue.json) and the sender thread."""

    def __init__(self, cfg_path: Path, rows) -> None:
        self.cfg_path = Path(cfg_path)
        self.rows = rows                 # () -> {head_no: (hex, a)}, the desk's looks now
        self.links: dict[int, str] = {}  # head_no -> lamp id (saved with the show)
        self._lock = threading.Lock()
        self.host, self.user = "", ""
        self.sent = 0
        self.error = None
        self._last: dict[str, dict] = {}
        self._stop = threading.Event()
        self._thread = None
        try:
            raw = json.loads(self.cfg_path.read_text(encoding="utf-8"))
            self.host, self.user = str(raw.get("host") or ""), str(raw.get("user") or "")
        except (OSError, ValueError, AttributeError):
            pass

    def _save(self) -> None:
        try:
            self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
            self.cfg_path.write_text(json.dumps({"host": self.host, "user": self.user}), encoding="utf-8")
        except OSError:
            pass

    def bridge(self) -> Bridge:
        if not self.host or not self.user:
            raise HueError("pair a Hue bridge first (Settings -> Output -> Philips Hue)")
        return Bridge(self.host, self.user)

    def status(self) -> dict:
        with self._lock:
            return {"host": self.host, "paired": bool(self.host and self.user), "links": len(self.links),
                    "running": bool(self._thread and self._thread.is_alive()), "sent": self.sent,
                    "error": self.error}

    def pair(self, host: str) -> dict:
        b = Bridge(host)
        user = b.pair()
        with self._lock:
            self.host, self.user, self.error = b.host, user, None
            self._last.clear()
        self._save()
        self.wake()
        return self.status()

    def forget(self) -> dict:
        with self._lock:
            self.host, self.user = "", ""
            self._last.clear()
        self._save()
        self.stop()
        return self.status()

    def set_links(self, links: dict) -> None:
        with self._lock:
            self.links = dict(links)
            self._last.clear()            # send everything once more
        self.wake()

    # -- the sender --------------------------------------------------------
    def wake(self) -> None:
        if not (self.host and self.user and self.links):
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="haze-hue", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3)

    def _loop(self) -> None:
        """Every tick: the lamps whose look changed; one change per 1/RATE
        seconds, the lamp that waited longest first (a big change reaches
        every lamp, a few at a time, never more than the bridge takes)."""
        sent_at: dict[str, float] = {}
        while not self._stop.is_set():
            with self._lock:
                links, last = dict(self.links), self._last
                host, user = self.host, self.user
            if not (links and host and user):
                return
            try:
                looks = self.rows()
            except Exception:
                looks = {}
            todo = []
            for head, lamp in links.items():
                hexcol, a = looks.get(head, ("#000000", 0.0))
                want = lamp_state(hexcol, a)
                if last.get(lamp) != want:
                    todo.append((sent_at.get(lamp, 0.0), lamp, want))
            if not todo:
                self._stop.wait(TICK_S)
                continue
            _, lamp, want = min(todo, key=lambda t: (t[0], t[1]))
            sent_at[lamp] = time.monotonic()
            try:
                Bridge(host, user).set(lamp, dict(want, transitiontime=1))   # 100 ms: smooth between steps
                with self._lock:
                    last[lamp] = want
                    self.sent += 1
                    self.error = None
            except HueError as exc:
                with self._lock:
                    self.error = str(exc)
                    last[lamp] = want            # don't hammer a lamp that refuses
                if "no Hue bridge" in str(exc):
                    self._stop.wait(2.0)         # the bridge is gone: try again soon
                    with self._lock:
                        last.clear()
                    continue
            self._stop.wait(1.0 / RATE)
