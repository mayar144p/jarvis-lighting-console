"""Philips Hue: the desk's lights drive Hue lamps (see app/hue.py).

Which desk light drives which lamp is saved with the show; the bridge's
address and key stay on this computer (DATA/hue.json)."""
from __future__ import annotations

from app import hue as hue_mod


class HueMixin:
    def _hue(self) -> hue_mod.HueLink:
        h = self.__dict__.get("_hue_obj")
        if h is None:
            h = self._hue_obj = hue_mod.HueLink(self.show_dir.parent / "hue.json", self._hue_rows)
        return h

    def _hue_rows(self) -> dict:
        with self.lock:
            return {row["n"]: (row["hex"], row["a"]) for row in self._looks()}

    def hue_public(self) -> dict:
        h = self.__dict__.get("_hue_obj")
        st = h.status() if h else {"host": "", "paired": False, "links": 0, "running": False, "sent": 0, "error": None}
        st["map"] = {str(k): v for k, v in sorted((self.__dict__.get("hue_links") or {}).items())}
        return st

    def _hue_apply(self) -> None:
        links = self.__dict__.get("hue_links") or {}
        h = self.__dict__.get("_hue_obj")
        if h is None and not links:
            return
        self._hue().set_links(links)

    # -- actions -------------------------------------------------------------
    def _a_hue(self, forget=False, **_):
        """Philips Hue: where things stand (paired, lamps linked, errors);
        forget=True unpairs this computer."""
        h = self._hue()
        if forget:
            h.forget()
            return {"hue": self.hue_public(), "summary": "Hue bridge forgotten"}
        st = self.hue_public()
        return {"hue": st, "summary": (f"Hue bridge {st['host']}, {st['links']} lamp(s) linked" if st["paired"]
                                       else "no Hue bridge paired")}

    def _a_hue_find(self, **_):
        """Look for Hue bridges on this network."""
        found = hue_mod.discover()
        return {"bridges": found, "summary": (f"found {len(found)} Hue bridge(s)" if found
                                              else "no Hue bridge found: type its address (the Hue app: Settings -> My Hue system)")}

    def _a_hue_pair(self, host="", **_):
        """Pair with the bridge at host (press its round button first)."""
        st = self._hue().pair(host)
        self._hue_apply()
        return {"hue": self.hue_public(), "summary": f"paired with the Hue bridge at {st['host']}"}

    def _a_hue_lights(self, **_):
        """The lamps the paired bridge knows."""
        lamps = self._hue().bridge().lights()
        return {"lamps": lamps, "summary": f"{len(lamps)} Hue lamp(s)"}

    def _a_hue_flash(self, lamp="", **_):
        """Make one Hue lamp blink, to find it in the room."""
        self._hue().bridge().set(str(lamp), {"alert": "select"})
        return {"summary": f"Hue lamp {lamp} blinks once"}

    def _a_hue_link(self, head=None, lamp=None, **_):
        """Drive Hue lamp `lamp` from desk light `head` (lamp empty: unlink)."""
        n = int(head)
        if not any(x["head_no"] == n for x in self.patch):
            raise ValueError(f"no light {n} on the desk")
        links = dict(self.__dict__.get("hue_links") or {})
        lamp = str(lamp or "").strip()
        if lamp:
            if not lamp.isdigit() or len(lamp) > 6:
                raise ValueError("a Hue lamp is a number (pick it from the list)")
            for k, v in list(links.items()):
                if v == lamp:                    # one lamp follows one light
                    del links[k]
            links[n] = lamp
        else:
            links.pop(n, None)
        self.hue_links = links
        self._hue_apply()
        return {"hue": self.hue_public(),
                "summary": f"light {n} drives Hue lamp {lamp}" if lamp else f"light {n} drives no Hue lamp"}

    # -- the show ------------------------------------------------------------
    def _hue_saved(self) -> dict:
        return {"links": {str(k): v for k, v in (self.__dict__.get("hue_links") or {}).items()}}

    def _hue_load(self, raw) -> None:
        links = {}
        if isinstance(raw, dict) and isinstance(raw.get("links"), dict):
            for k, v in raw["links"].items():
                try:
                    if str(v).isdigit():
                        links[int(k)] = str(v)
                except (TypeError, ValueError):
                    continue
        self.hue_links = links
        self._hue_apply()

    def _hue_stop(self) -> None:
        h = self.__dict__.get("_hue_obj")
        if h:
            h.stop()
