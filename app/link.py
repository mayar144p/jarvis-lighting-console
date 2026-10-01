"""Ableton Link: follow the tempo and the beat of a Link session (Ableton
Live, Traktor, rekordbox, Serato, djay, Bitwig, phone apps ...) on the
same network.  Listen-only: the desk joins no session of its own, it
reads what the peers announce.

Two parts of the Link wire format (big-endian throughout):

* **discovery** - every peer multicasts "alive" messages to
  224.76.78.75:20808: the header "_asdp_v\\x01", a message header
  (type, ttl, group, node id) and payload entries (a 4-byte key, a
  4-byte size, the value).  The ones read here:
      tmln  tempo (microseconds a beat), beat origin (micro-beats),
            time origin (microseconds of the session's "ghost" clock)
      sess  the session id (8 bytes)
      mep4  where the peer answers clock measurements (IPv4, port)
* **measurement** - "_link_v\\x01" pings to that endpoint carry our clock
  (htms); the pong carries the session's ghost time (__gt) and echoes
  ours, so ghost - (sent + received) / 2 is the offset between the
  session's clock and ours.

With the timeline and the offset: the beat at any moment of our clock.
Pure: sockets and threads are in the engine (app/engine_tempo.py).
"""
from __future__ import annotations

import socket
import statistics
import struct

GROUP = "224.76.78.75"
PORT = 20808
DISCOVERY = b"_asdp_v\x01"
MEASURE = b"_link_v\x01"
ALIVE, RESPONSE, BYEBYE = 1, 2, 3
PING, PONG = 1, 2
TIMELINE, SESSION, START_STOP, MEP4 = b"tmln", b"sess", b"stst", b"mep4"
HOST_TIME, GHOST_TIME, PREV_GHOST = b"htms", b"__gt", b"_pgt"
# a peer not heard from this long has left
PEER_TIMEOUT_S = 5.0
SAMPLES = 25


def entries(data: bytes) -> dict[bytes, bytes]:
    """The payload entries {key: value}; a truncated one ends the list."""
    out, i = {}, 0
    while i + 8 <= len(data):
        key = data[i:i + 4]
        (size,) = struct.unpack_from(">I", data, i + 4)
        i += 8
        if i + size > len(data):
            break
        out[key] = data[i:i + size]
        i += size
    return out


def entry(key: bytes, value: bytes) -> bytes:
    return key + struct.pack(">I", len(value)) + value


def parse_discovery(pkt: bytes) -> dict | None:
    """A discovery message, or None if it isn't one."""
    if not pkt.startswith(DISCOVERY) or len(pkt) < len(DISCOVERY) + 12:
        return None
    i = len(DISCOVERY)
    kind, ttl, group = struct.unpack_from(">BBH", pkt, i)
    node = pkt[i + 4:i + 12]
    ents = entries(pkt[i + 12:])
    out = {"type": kind, "ttl": ttl, "group": group, "node": node.hex()}
    tl = ents.get(TIMELINE)
    if tl and len(tl) >= 24:
        mpb, beat_origin, time_origin = struct.unpack_from(">qqq", tl)
        if mpb > 0:
            out["timeline"] = (mpb, beat_origin, time_origin)
    if ents.get(SESSION) and len(ents[SESSION]) >= 8:
        out["session"] = ents[SESSION][:8].hex()
    ep = ents.get(MEP4)
    if ep and len(ep) >= 6:
        addr, port = struct.unpack_from(">IH", ep)
        out["mep4"] = (socket.inet_ntoa(struct.pack(">I", addr)), port)
    st = ents.get(START_STOP)
    if st and len(st) >= 1:
        out["playing"] = bool(st[0])
    return out


def build_alive(node: bytes, bpm: float, beat_origin: float, time_origin_us: int,
                session: bytes | None = None, mep4: tuple[str, int] | None = None, ttl: int = 5) -> bytes:
    """An alive message as a peer sends it (the tests' fake peer)."""
    payload = entry(TIMELINE, struct.pack(">qqq", int(round(60e6 / bpm)), int(round(beat_origin * 1e6)),
                                          int(time_origin_us)))
    payload += entry(SESSION, (session or node)[:8].ljust(8, b"\0"))
    if mep4:
        payload += entry(MEP4, socket.inet_aton(mep4[0]) + struct.pack(">H", mep4[1]))
    return DISCOVERY + struct.pack(">BBH", ALIVE, ttl, 0) + node[:8].ljust(8, b"\0") + payload


def build_ping(host_us: int, prev_ghost_us: int = 0) -> bytes:
    payload = entry(HOST_TIME, struct.pack(">q", int(host_us)))
    if prev_ghost_us:
        payload += entry(PREV_GHOST, struct.pack(">q", int(prev_ghost_us)))
    return MEASURE + bytes([PING]) + payload


def parse_ping(pkt: bytes) -> dict | None:
    if not pkt.startswith(MEASURE) or len(pkt) < len(MEASURE) + 1 or pkt[len(MEASURE)] != PING:
        return None
    ents = entries(pkt[len(MEASURE) + 1:])
    if HOST_TIME not in ents:
        return None
    return {"host": struct.unpack(">q", ents[HOST_TIME][:8])[0], "payload": pkt[len(MEASURE) + 1:]}


def build_pong(session: bytes, ghost_us: int, ping_payload: bytes) -> bytes:
    """What a peer answers a ping with: its session, its ghost time now,
    and the ping's own entries back."""
    return (MEASURE + bytes([PONG]) + entry(SESSION, session[:8].ljust(8, b"\0"))
            + entry(GHOST_TIME, struct.pack(">q", int(ghost_us))) + ping_payload)


def parse_pong(pkt: bytes) -> dict | None:
    if not pkt.startswith(MEASURE) or len(pkt) < len(MEASURE) + 1 or pkt[len(MEASURE)] != PONG:
        return None
    ents = entries(pkt[len(MEASURE) + 1:])
    if GHOST_TIME not in ents or HOST_TIME not in ents:
        return None
    return {"ghost": struct.unpack(">q", ents[GHOST_TIME][:8])[0],
            "host": struct.unpack(">q", ents[HOST_TIME][:8])[0],
            "session": ents[SESSION][:8].hex() if SESSION in ents else None}


class Follower:
    """The session as we know it: the latest timeline, the clock offset,
    the peers.  Times are our clock in microseconds."""

    def __init__(self):
        self.peers: dict[str, dict] = {}       # node -> {at, session, mep4, timeline}
        self.session: str | None = None
        self.timeline: tuple[int, int, int] | None = None
        self._offsets: list[int] = []

    def alive(self, msg: dict, now_us: int) -> None:
        if msg["type"] == BYEBYE:
            self.peers.pop(msg["node"], None)
            return
        if msg["type"] not in (ALIVE, RESPONSE) or "timeline" not in msg:
            return
        sess = msg.get("session") or msg["node"]
        if self.session and sess != self.session and any(
                p["session"] == self.session and now_us - p["at"] < PEER_TIMEOUT_S * 1e6 for p in self.peers.values()):
            return                               # another session: keep to the one we follow
        if sess != self.session:
            self.session, self._offsets = sess, []
        self.peers[msg["node"]] = {"at": now_us, "session": sess, "mep4": msg.get("mep4")}
        self.timeline = msg["timeline"]

    def pong(self, msg: dict, now_us: int) -> None:
        if msg.get("session") and self.session and msg["session"] != self.session:
            return
        if now_us - msg["host"] > 500_000 or now_us < msg["host"]:
            return                               # a stale or nonsense answer
        self._offsets.append(msg["ghost"] - (msg["host"] + now_us) // 2)
        self._offsets = self._offsets[-SAMPLES:]

    def endpoints(self, now_us: int) -> list[tuple[str, int]]:
        return [p["mep4"] for p in self.peers.values()
                if p.get("mep4") and p["session"] == self.session and now_us - p["at"] < PEER_TIMEOUT_S * 1e6]

    def connected(self, now_us: int) -> int:
        return sum(1 for p in self.peers.values()
                   if p["session"] == self.session and now_us - p["at"] < PEER_TIMEOUT_S * 1e6)

    @property
    def offset(self) -> int | None:
        return int(statistics.median(self._offsets)) if self._offsets else None

    def bpm(self) -> float | None:
        return 60e6 / self.timeline[0] if self.timeline else None

    def beats(self, now_us: int) -> float | None:
        """The session's beat at our time, once the clocks are measured."""
        if not self.timeline or self.offset is None:
            return None
        mpb, beat_origin, time_origin = self.timeline
        ghost = now_us + self.offset
        return beat_origin / 1e6 + (ghost - time_origin) / mpb
