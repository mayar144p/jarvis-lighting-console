"""Effects that paint the rig as pixels (app/pixels.py): a gradient of
colours across the room, and a picture or a video laid over the lights.
Pixel bars and panels count cell by cell.

Pictures are kept with the show (small: the browser shrinks them to at
most 96 x 96).  A video stays in the browser that plays it: that screen
sends its frames here while it plays, and the lights show the latest one.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import base64
import os
import time

from app import merge
from app import pixels as pix_mod
from app.engine_base import _fclamp

# a video frame older than this is a video nobody plays any more
FRAME_STALE_S = 2.0
MAX_MEDIA = 24


class PixelsMixin:
    def _media(self) -> dict:
        m = self.__dict__.get("media")
        if m is None:
            m = self.media = {}
        return m

    def media_public(self) -> list[dict]:
        now = time.monotonic()
        live = self.__dict__.get("_media_live") or {}
        return [{"id": k, "name": v["name"], "kind": v["kind"], "w": v.get("w"), "h": v.get("h"),
                 "playing": k in live and now - live[k][3] < FRAME_STALE_S}
                for k, v in self._media().items()]

    # -- actions --------------------------------------------------------------
    def _pix_heads(self, heads, group) -> list[int]:
        rows = self._fx_targets(heads, group)
        nums = [h["head_no"] for h in rows if pix_mod.units([h])]
        if not nums:
            raise ValueError("none of those lights can change colour")
        return nums

    def _pix_start(self, kind: str, params: dict, nums: list[int]) -> int:
        if len(self.fx) >= 16:
            raise ValueError("too many effects running - stop some first")
        # one painting at a time on a light: a new one takes over
        self.fx = [f for f in self.fx if not (f.get("pix") and set(f["heads"]) & set(nums)
                                              and not f.get("cue_pb"))]
        self._fx_seq += 1
        self.fx.append({"id": self._fx_seq, "pix": kind, "params": dict(params), "heads": list(nums),
                        "t0": time.monotonic(), "duration": None})
        return self._fx_seq

    def _a_run_gradient(self, colours=None, space="left-right", speed=0.0, beats=None,
                        heads=None, group=None, **_):
        """Colours across the room: two or more, in a direction (left-right,
        stage-out, up, centre-out, around ...), still (speed 0) or
        scrolling (rounds a second, or locked to `beats`)."""
        raw = colours if isinstance(colours, (list, tuple)) else str(colours or "").replace(" and ", ",").split(",")
        names = dict(__import__("app.showdesign", fromlist=["COLOR_NAMES"]).COLOR_NAMES)
        hexes = []
        for c in raw:
            c = str(c).strip().lower()
            if not c:
                continue
            hx = c if c.startswith("#") and len(c) in (4, 7) else names.get(c)
            if hx is None:
                raise ValueError(f"which colour is {c!r}?")
            hexes.append(hx)
        if not 2 <= len(hexes) <= 6:
            raise ValueError("a gradient is 2 to 6 colours")
        space = str(space or "left-right")
        if space not in pix_mod.SPACES:
            raise ValueError(f"direction is one of {', '.join(pix_mod.SPACES)}")
        nums = self._pix_heads(heads, group)
        p = {"colours": hexes, "space": space, "speed": _fclamp(speed or 0, 0, 4)}
        if beats not in (None, "", 0, "0", False):
            p["beats"] = self._clean_beats(beats)
            p["speed"] = p["speed"] or 1.0
        fid = self._pix_start("gradient", p, nums)
        n = len(pix_mod.units([h for h in self.patch if h["head_no"] in set(nums)]))
        return {"fx": fid, "heads": len(nums),
                "summary": f"gradient on {len(nums)} light(s), {n} pixel(s), {space}"
                           + (" scrolling" if p["speed"] else "")}

    def _a_media_save(self, name="", w=None, h=None, data=None, kind="image", id=None, **_):
        """Keep a picture with the show (the browser sends it shrunk: w x h
        RGB, base64).  kind=video keeps only its name: the screen that
        plays it sends the frames."""
        kind = str(kind or "image")
        if kind not in ("image", "video"):
            raise ValueError("kind is image or video")
        media = self._media()
        ident = str(id or "") or "m" + os.urandom(3).hex()
        if ident not in media and len(media) >= MAX_MEDIA:
            raise ValueError(f"at most {MAX_MEDIA} pictures and videos")
        entry = {"name": str(name or kind).strip()[:40] or kind, "kind": kind}
        if kind == "image":
            ww, hh, raw = pix_mod.clean_media(w, h, data)
            entry.update({"w": ww, "h": hh, "data": base64.b64encode(raw).decode("ascii")})
        media[ident] = entry
        return {"id": ident, "media": self.media_public(), "summary": f"{entry['name']} kept with the show"}

    def _a_media_delete(self, id=None, **_):
        if str(id) not in self._media():
            raise ValueError(f"no picture {id!r}")
        self._media().pop(str(id))
        self.fx = [f for f in self.fx if not (f.get("pix") == "media" and f["params"].get("media") == str(id))]
        return {"media": self.media_public(), "summary": "picture deleted"}

    def _a_run_media(self, id=None, view="front", heads=None, group=None, **_):
        """Lay a picture (or a playing video) over the lights, seen from the
        front or from above: each pixel takes the colour under it."""
        if str(id) not in self._media():
            raise ValueError(f"no picture {id!r}")
        if view not in pix_mod.VIEWS:
            raise ValueError("view is front or top")
        nums = self._pix_heads(heads, group)
        fid = self._pix_start("media", {"media": str(id), "view": view}, nums)
        return {"fx": fid, "heads": len(nums),
                "summary": f"{self._media()[str(id)]['name']} on {len(nums)} light(s), from the {'front' if view == 'front' else 'top'}"}

    # -- the feed: a video's frames (not an action: 25 a second) ---------------
    def media_frame(self, id, w, h, data) -> dict:
        ww, hh, raw = pix_mod.clean_media(w, h, data)
        with self.lock:
            if str(id) not in self._media():
                raise ValueError(f"no video {id!r}")
            live = self.__dict__.setdefault("_media_live", {})
            live[str(id)] = (ww, hh, raw, time.monotonic())
        return {"ok": True}

    def _media_pixels(self, ident: str):
        live = (self.__dict__.get("_media_live") or {}).get(ident)
        if live:
            return live[0], live[1], live[2]          # the latest frame (held when it stops)
        m = self._media().get(ident)
        if not m or m.get("kind") != "image":
            return None
        cache = self.__dict__.setdefault("_media_raw", {})
        got = cache.get(ident)
        if got is None or got[0] is not m:
            got = cache[ident] = (m, base64.b64decode(m["data"]))
        return m["w"], m["h"], got[1]

    # -- one frame -------------------------------------------------------------
    def _pix_units(self, row: dict) -> list[dict]:
        key = (self.patch_rev, tuple(row["heads"]))
        cached = row.get("_units")
        if cached and cached[0] == key:
            return cached[1]
        by_no = {h["head_no"]: h for h in self.patch}
        us = pix_mod.units([by_no[n] for n in row["heads"] if n in by_no])
        row["_units"] = (key, us)
        return us

    def _pix_values(self, row: dict, rounds: float, out: dict) -> None:
        us = self._pix_units(row)
        if not us:
            return
        p = row["params"]
        by_no = {h["head_no"]: h for h in self.patch}
        if row["pix"] == "gradient":
            ckey = ("g", p["space"], self.patch_rev, tuple(row["heads"]))
            if row.get("_along", (None,))[0] != ckey:
                row["_along"] = (ckey, pix_mod.along(us, p["space"]))
            pos = row["_along"][1]
            cols = [pix_mod.gradient_at(p["colours"], pix_mod.scroll(f, rounds) if p.get("speed") else f) for f in pos]
        else:
            got = self._media_pixels(p["media"])
            if got is None:
                return
            w, h, raw = got
            ckey = ("m", p["view"], self.patch_rev, tuple(row["heads"]))
            if row.get("_plane", (None,))[0] != ckey:
                row["_plane"] = (ckey, pix_mod.plane(us, p["view"]))
            cols = [pix_mod.sample(w, h, raw, u, v) for u, v in row["_plane"][1]]
        for unit, (r, g, b) in zip(us, cols):
            head = by_no.get(unit["n"])
            if head is None:
                continue
            vals = self._colour_values(head, "#%02x%02x%02x" % (r, g, b))
            dst = out.setdefault(unit["n"], {})
            k = unit["k"]
            if k is None:
                dst.update(vals)
                continue
            reps = merge._repeated(head["map"])
            for role, v in vals.items():
                if role in reps:
                    if k <= reps[role]:
                        dst[f"{role}@{k}"] = v
                elif k == 1:
                    dst[role] = v               # a single channel of the light, once
