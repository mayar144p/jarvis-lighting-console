"""Draws the Stream Deck plugin's key pictures (plain PNGs, no packages):
python tools/streamdeck_icons.py  ->  web/streamdeck/com.haze.desk.sdPlugin/imgs/"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "web" / "streamdeck" / "com.haze.desk.sdPlugin" / "imgs"
BG = (24, 26, 31)


def png(w: int, h: int, px) -> bytes:
    raw = b"".join(b"\x00" + bytes(c for x in range(w) for c in px(x, y)) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# Shapes in a 0..1 square: a function (u, v) -> coverage 0..1.
def disc(cx, cy, r):
    return lambda u, v: 1.0 if (u - cx) ** 2 + (v - cy) ** 2 <= r * r else 0.0


def ring(cx, cy, r, t):
    return lambda u, v: 1.0 if abs(math.hypot(u - cx, v - cy) - r) <= t / 2 else 0.0


def box(x0, y0, x1, y1):
    return lambda u, v: 1.0 if x0 <= u <= x1 and y0 <= v <= y1 else 0.0


def tri(ax, ay, bx, by, cx, cy):
    def f(u, v):
        d1 = (u - bx) * (ay - by) - (ax - bx) * (v - by)
        d2 = (u - cx) * (by - cy) - (bx - cx) * (v - cy)
        d3 = (u - ax) * (cy - ay) - (cx - ax) * (v - ay)
        neg, pos = min(d1, d2, d3) < 0, max(d1, d2, d3) > 0
        return 0.0 if neg and pos else 1.0
    return f


def union(*fs):
    return lambda u, v: max(f(u, v) for f in fs)


ICONS = {   # name: (accent colour, shape)
    "go": ((46, 204, 113), tri(0.36, 0.26, 0.36, 0.74, 0.74, 0.5)),
    "back": ((241, 196, 15), union(tri(0.62, 0.28, 0.62, 0.72, 0.3, 0.5), box(0.66, 0.28, 0.72, 0.72))),
    "blackout": ((140, 145, 155), ring(0.5, 0.5, 0.24, 0.07)),
    "blackout-on": ((231, 76, 60), union(disc(0.5, 0.5, 0.27))),
    "button": ((52, 152, 219), box(0.24, 0.24, 0.76, 0.76)),
    "tap": ((155, 89, 182), union(disc(0.5, 0.5, 0.12), ring(0.5, 0.5, 0.27, 0.05))),
    "macro": ((26, 188, 156), union(box(0.24, 0.3, 0.76, 0.38), box(0.24, 0.46, 0.66, 0.54), box(0.24, 0.62, 0.56, 0.7))),
    "master": ((236, 240, 241), union(box(0.47, 0.2, 0.53, 0.8), box(0.34, 0.36, 0.66, 0.46))),
    "fader": ((52, 152, 219), union(box(0.47, 0.2, 0.53, 0.8), box(0.34, 0.54, 0.66, 0.64))),
    "plugin": ((241, 196, 15), union(disc(0.5, 0.42, 0.2), box(0.42, 0.6, 0.58, 0.76))),
    "category": ((241, 196, 15), union(disc(0.5, 0.42, 0.2), box(0.42, 0.6, 0.58, 0.76))),
}
SIZES = {"plugin": 256, "category": 28}


def draw(colour, shape, size, ss=3, lift=0.0):
    def px(x, y):
        a = sum(shape((x + (i + .5) / ss) / size, (y + (j + .5) / ss) / size + lift)
                for i in range(ss) for j in range(ss)) / (ss * ss)
        bg = BG if size >= 40 else None             # small list icons: transparent
        if bg is None:
            return (*colour, round(255 * a))
        return (*(round(bg[k] + (colour[k] - bg[k]) * a) for k in range(3)), 255)
    return png(size, size, px)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (colour, shape) in ICONS.items():
        base = SIZES.get(name, 72)
        lift = 0.0 if name in SIZES else 0.1          # key pictures: room for the title below
        (OUT / f"{name}.png").write_bytes(draw(colour, shape, base, lift=lift))
        (OUT / f"{name}@2x.png").write_bytes(draw(colour, shape, base * 2, lift=lift))
    print(f"{len(ICONS) * 2} pictures in {OUT}")


if __name__ == "__main__":
    main()
