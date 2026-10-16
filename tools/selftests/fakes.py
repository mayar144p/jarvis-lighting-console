"""Test doubles and packet builders: what a fake DJ deck, Link peer, MIDI
port or OSC sender would send, and small GDTF-geometry helpers.  Only the
selftests use these, so they live here and not in the app."""
from __future__ import annotations

import queue
import socket
import struct

from app.link import (ALIVE, DISCOVERY, GHOST_TIME, HOST_TIME, MEASURE, MEP4, PING, PONG, SESSION, TIMELINE,
                      entries, entry)
from app.tempo import F_MASTER, F_PLAYING, PRODJ_BEAT, PRODJ_MAGIC, PRODJ_STATUS


def matrix_translation(m: list[float]) -> tuple[float, float, float]:
    """Translation of a ROW-VECTOR matrix: indices 3, 7, 11."""
    return (m[3], m[7], m[11])


def mat_mul(a: list[float], b: list[float]) -> list[float]:
    """Row-vector multiply: the transform for `a` THEN `b` is `b * a`."""
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[r * 4 + c] = sum(a[k * 4 + c] * b[r * 4 + k] for k in range(4))
    return out


def node_at(root: dict, path: str | None) -> dict | None:
    if not path:
        return None
    node = None
    for i, n in enumerate(root.get("nodes") or []):
        if str(i) == path:
            return n
    parts = path.split("/")
    node = (root.get("nodes") or [None])[int(parts[0])] if root.get("nodes") else None
    for p in parts[1:]:
        if not node:
            return None
        node = (node.get("children") or [None])[int(p)]
    return node


def build_alive(node: bytes, bpm: float, beat_origin: float, time_origin_us: int,
                session: bytes | None = None, mep4: tuple[str, int] | None = None, ttl: int = 5) -> bytes:
    """An alive message as a peer sends it (the tests' fake peer)."""
    payload = entry(TIMELINE, struct.pack(">qqq", int(round(60e6 / bpm)), int(round(beat_origin * 1e6)),
                                          int(time_origin_us)))
    payload += entry(SESSION, (session or node)[:8].ljust(8, b"\0"))
    if mep4:
        payload += entry(MEP4, socket.inet_aton(mep4[0]) + struct.pack(">H", mep4[1]))
    return DISCOVERY + struct.pack(">BBH", ALIVE, ttl, 0) + node[:8].ljust(8, b"\0") + payload


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


class FakeMidiSource:
    """Test double: same queue shapes the winmm callback produces."""

    kind = "fake"

    def __init__(self):
        self.queue: queue.Queue = queue.Queue()
        self.closed = False

    def push(self, status: int, data1: int, data2: int) -> None:
        self.queue.put(("msg", status, data1, data2))

    def push_close(self) -> None:
        self.queue.put(("close",))

    def close(self) -> None:
        self.closed = True


def bundle(*messages: bytes) -> bytes:
    out = b"#bundle\0" + struct.pack(">Q", 1)
    for m in messages:
        out += struct.pack(">i", len(m)) + m
    return out


def build_prodj_status(device: int, master: bool = False, playing: bool = True, name: str = "CDJ-3000",
                       track: int = 0, beat: int | None = None, pitch_pct: float = 0.0) -> bytes:
    """A CDJ status packet: who's master and playing, the loaded track's
    rekordbox id, the beat it is on and its speed (tests, the virtual deck)."""
    p = bytearray(0xD4)
    p[:10] = PRODJ_MAGIC
    p[0x0A] = PRODJ_STATUS
    p[0x0B:0x0B + len(name)] = name.encode("ascii")[:20]
    p[0x21] = device & 0xFF
    p[0x2C:0x30] = int(track).to_bytes(4, "big")
    p[0x89] = (F_PLAYING if playing else 0) | (F_MASTER if master else 0)
    p[0x8C:0x90] = int(round(0x100000 * (1 + pitch_pct / 100.0))).to_bytes(4, "big")
    p[0xA0:0xA4] = (0xFFFFFFFF if beat is None else int(beat)).to_bytes(4, "big")
    return bytes(p)


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


def build_mtc(seconds: float, fps: float = 25.0) -> list[int]:
    """The eight quarter-frame data bytes for a time (tests, a virtual source)."""
    rate = {24.0: 0, 25.0: 1, 29.97: 2, 30.0: 3}[fps]
    fr = int(round((seconds % 1) * fps))
    s, m, h = int(seconds % 60), int(seconds // 60 % 60), int(seconds // 3600)
    vals = [fr & 15, fr >> 4, s & 15, s >> 4, m & 15, m >> 4, h & 15, (h >> 4) | (rate << 1)]
    return [(i << 4) | v for i, v in enumerate(vals)]
