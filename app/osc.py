"""OSC (Open Sound Control) for TouchOSC, Bitfocus Companion, QLab ...:
the message format (UDP), parse and build.  Pure stdlib.

A message is an address ("/jarvis/go"), a type tag string (",if") and
the arguments: i int32, f float32, s string, T/F true/false, N nil.
Bundles ("#bundle") hold messages (and bundles); the time tag is
ignored - everything happens as it arrives.
"""
from __future__ import annotations

import struct


def _pad(n: int) -> int:
    return (n + 4) & ~3


def _read_str(data: bytes, i: int) -> tuple[str, int]:
    end = data.index(b"\0", i)
    return data[i:end].decode("utf-8", "replace"), _pad(end)


def parse(data: bytes) -> list[tuple[str, list]]:
    """Every (address, args) in a packet (a bundle can hold many); a
    malformed packet (no terminator, a missing argument) is nothing."""
    try:
        return _parse(data)
    except (ValueError, struct.error, UnicodeDecodeError, IndexError):
        return []


def _parse(data: bytes) -> list[tuple[str, list]]:
    if data.startswith(b"#bundle\0"):
        out = []
        i = 16                                          # "#bundle\0" + time tag
        while i + 4 <= len(data):
            (size,) = struct.unpack_from(">i", data, i)
            i += 4
            if size <= 0 or i + size > len(data):
                break
            out += _parse(data[i:i + size])
            i += size
        return out
    if not data.startswith(b"/"):
        return []
    addr, i = _read_str(data, 0)
    if i >= len(data) or data[i:i + 1] != b",":
        return [(addr, [])]
    tags, i = _read_str(data, i)
    args: list = []
    for t in tags[1:]:
        if t == "i":
            args.append(struct.unpack_from(">i", data, i)[0])
            i += 4
        elif t == "f":
            args.append(round(struct.unpack_from(">f", data, i)[0], 6))
            i += 4
        elif t == "s":
            s, i = _read_str(data, i)
            args.append(s)
        elif t == "T":
            args.append(True)
        elif t == "F":
            args.append(False)
        elif t == "N":
            args.append(None)
        elif t == "d":
            args.append(struct.unpack_from(">d", data, i)[0])
            i += 8
        elif t == "h":
            args.append(struct.unpack_from(">q", data, i)[0])
            i += 8
        else:
            break                                       # a type we don't read: stop here
    return [(addr, args)]


def _s(text: str) -> bytes:
    raw = text.encode("utf-8") + b"\0"
    return raw + b"\0" * (_pad(len(raw) - 1) - len(raw))


def build(addr: str, *args) -> bytes:
    tags, body = ",", b""
    for a in args:
        if a is True:
            tags += "T"
        elif a is False:
            tags += "F"
        elif a is None:
            tags += "N"
        elif isinstance(a, int):
            tags += "i"
            body += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            body += struct.pack(">f", a)
        else:
            tags += "s"
            body += _s(str(a))
    return _s(addr) + _s(tags) + body


def bundle(*messages: bytes) -> bytes:
    out = b"#bundle\0" + struct.pack(">Q", 1)
    for m in messages:
        out += struct.pack(">i", len(m)) + m
    return out
