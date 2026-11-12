"""The desk's own name on the network: haze.local.

A phone remote kept on the home screen opens one address.  At another venue
the desk computer has another Wi-Fi address, and an icon made for the old
one just keeps loading.  So the desk answers to a name that doesn't change -
haze.local, by multicast DNS (what printers and Apple devices use to find
each other, RFC 6762) - and the remote moves itself to that name once it
knows the phone can reach it there.  Then one home-screen icon opens the
desk at any venue.

Only the name is answered (an A record: this computer's address on the
network the question came from); nothing is advertised beyond it, and
nothing but the name is read from the network.  Stdlib only.  Another Haze
already answering to the name on the same network: this one takes
haze-2.local (and so on).
"""

from __future__ import annotations

import ipaddress
import os
import socket
import struct
import threading
import time

GROUP, PORT = "224.0.0.251", 5353
TTL = 120

_state: dict = {"name": "", "thread": None, "stop": None, "error": ""}


def name() -> str:
    """The name the desk answers to now ("haze.local"), or "" when it doesn't."""
    return _state["name"]


def error() -> str:
    return _state["error"]


# -- the wire format ----------------------------------------------------------
def _read_name(buf: bytes, off: int, depth: int = 0) -> tuple[str, int]:
    labels, end = [], None
    while True:
        if off >= len(buf) or depth > 20:
            raise ValueError("bad name")
        n = buf[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:                       # a pointer to an earlier name
            ptr = struct.unpack_from("!H", buf, off)[0] & 0x3FFF
            sub, _ = _read_name(buf, ptr, depth + 1)
            labels.append(sub)
            off += 2
            end = off
            return ".".join(x for x in labels if x), end
        labels.append(buf[off + 1:off + 1 + n].decode("utf-8", "replace"))
        off += 1 + n
    return ".".join(labels), off


def _encode_name(host: str) -> bytes:
    out = b""
    for label in host.rstrip(".").split("."):
        raw = label.encode("utf-8")[:63]
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def questions(buf: bytes) -> list[tuple[str, int, bool]]:
    """(name, type, unicast-response wanted) of each question in a query."""
    if len(buf) < 12:
        return []
    _id, flags, qd, _an, _ns, _ar = struct.unpack_from("!6H", buf, 0)
    if flags & 0x8000:                             # a response, not a query
        return []
    out, off = [], 12
    for _ in range(min(qd, 32)):
        qname, off = _read_name(buf, off)
        qtype, qclass = struct.unpack_from("!HH", buf, off)
        off += 4
        out.append((qname.lower(), qtype, bool(qclass & 0x8000)))
    return out


def answers(buf: bytes) -> list[tuple[str, str]]:
    """(name, address) of the A records in a response (to see who else has the name)."""
    if len(buf) < 12:
        return []
    _id, flags, qd, an, _ns, _ar = struct.unpack_from("!6H", buf, 0)
    if not flags & 0x8000:
        return []
    off = 12
    try:
        for _ in range(qd):
            _, off = _read_name(buf, off)
            off += 4
        out = []
        for _ in range(min(an, 64)):
            rname, off = _read_name(buf, off)
            rtype, _rclass, _ttl, rdlen = struct.unpack_from("!HHIH", buf, off)
            off += 10
            if rtype == 1 and rdlen == 4:
                out.append((rname.lower(), socket.inet_ntoa(buf[off:off + 4])))
            off += rdlen
        return out
    except (ValueError, struct.error):
        return []


def response(host: str, addr: str, qid: int = 0) -> bytes:
    """An answer: `host` is at `addr` (cache-flush set: this is the only one)."""
    head = struct.pack("!6H", qid, 0x8400, 0, 1, 0, 0)
    rr = _encode_name(host) + struct.pack("!HHIH", 1, 0x8001, TTL, 4) + socket.inet_aton(addr)
    return head + rr


def query(host: str) -> bytes:
    return struct.pack("!6H", 0, 0, 1, 0, 0, 0) + _encode_name(host) + struct.pack("!HH", 1, 1)


def pick_address(sender: str, mine: list[str]) -> str | None:
    """Of this computer's addresses, the one on the asker's network (the most
    leading bits in common); a VPN's address only for a VPN's asker."""
    try:
        s = int(ipaddress.ip_address(sender))
    except ValueError:
        return mine[0] if mine else None
    best, score = None, -1
    for a in mine:
        try:
            n = int(ipaddress.ip_address(a))
        except ValueError:
            continue
        common = 32 - (s ^ n).bit_length()
        if common > score:
            best, score = a, common
    return best if score >= 8 else (mine[0] if mine else None)


# -- the responder ------------------------------------------------------------
def _socket() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    s.bind(("", PORT))                              # (shared with the computer's own mDNS)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    return s


def _join(s: socket.socket, addrs: list[str]) -> None:
    for a in addrs or ["0.0.0.0"]:
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(GROUP) + socket.inet_aton(a))
        except OSError:
            pass                                    # (already joined on that network, or it's gone)


def start(base: str = "haze", addresses=None) -> str:
    """Answer to <base>.local from now on (a thread); returns the name, or ""
    when the network won't have it (the reason in error())."""
    from app import remote
    if _state["thread"] is not None:
        return _state["name"]
    addresses = addresses or remote.lan_addresses
    try:
        s = _socket()
    except OSError as exc:
        _state["error"] = f"can't use the name port ({exc})"
        return ""
    mine = addresses()
    _join(s, mine)
    s.settimeout(0.25)
    # the name: haze.local, unless another desk on this network has it
    host = f"{base}.local"
    for n in range(1, 10):
        host = f"{base}.local" if n == 1 else f"{base}-{n}.local"
        if not _taken(s, host, mine):
            break
    _state["name"] = host
    stop = threading.Event()
    _state["stop"] = stop

    def loop():
        joined, last_join = set(mine), time.monotonic()
        for addr in mine:                           # say it once, so caches pick it up now
            try:
                s.sendto(response(host, addr), (GROUP, PORT))
            except OSError:
                pass
        while not stop.is_set():
            if time.monotonic() - last_join > 30:   # (a new network since: listen there too)
                now = addresses()
                _join(s, [a for a in now if a not in joined])
                joined |= set(now)
                last_join = time.monotonic()
            try:
                buf, (src, sport) = s.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(0.5)
                continue
            try:
                qs = questions(buf)
            except (ValueError, struct.error):
                continue
            for qname, qtype, unicast in qs:
                if qname != host or qtype not in (1, 255):
                    continue
                addr = pick_address(src, addresses())
                if not addr:
                    continue
                pkt = response(host, addr, struct.unpack_from("!H", buf, 0)[0] if sport != PORT else 0)
                try:
                    # a one-shot asker (not from 5353) or one asking for a direct answer gets it directly
                    s.sendto(pkt, (src, sport) if unicast or sport != PORT else (GROUP, PORT))
                except OSError:
                    pass

    th = threading.Thread(target=loop, name="haze-mdns", daemon=True)
    _state["thread"] = th
    th.start()
    return host


def _taken(s: socket.socket, host: str, mine: list[str]) -> bool:
    """Does another computer answer to `host` (asked twice, a quarter second each)?"""
    for _ in range(2):
        try:
            s.sendto(query(host), (GROUP, PORT))
        except OSError:
            return False
        end = time.monotonic() + 0.25
        while time.monotonic() < end:
            try:
                buf, _src = s.recvfrom(9000)
            except (socket.timeout, OSError):
                break
            for rname, addr in answers(buf):
                if rname == host and addr not in mine:
                    return True
    return False


def stop() -> None:
    if _state["stop"] is not None:
        _state["stop"].set()
    _state.update(thread=None, stop=None, name="")


def enabled() -> bool:
    return os.environ.get("HAZE_NAME_ON_NETWORK", "true").strip().lower() not in ("0", "false", "no", "off")
