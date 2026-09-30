"""Sound-reactive control in the desk (app/sound.py): the browser's
listening, links from the sound to brightness and effect speed, triggers
that press buttons on the beat or the drop, and audio as a tempo source.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import threading
import time

from app import sound as sound_mod


class SoundMixin:
    def _sound_cfg(self) -> dict:
        cfg = self.__dict__.get("sound_cfg")
        if cfg is None:
            cfg = self.sound_cfg = sound_mod.empty_config()
        return cfg

    def _sound_live(self, now: float) -> dict | None:
        """The latest reading, or None when nobody is listening."""
        at = self.__dict__.get("_sound_at")
        if at is None or now - at > sound_mod.STALE_S:
            return None
        return self.__dict__.get("_sound")

    def sound_public(self) -> dict:
        now = time.monotonic()
        r = self._sound_live(now)
        cfg = self._sound_cfg()
        return {"listening": r is not None, "reading": r, "links": cfg["links"],
                "triggers": cfg["triggers"], "tempo": cfg["tempo"],
                "from": self.__dict__.get("_sound_from")}

    # -- the feed (not an action: 25 a second, no undo, no reload) ----------
    def sound_feed(self, raw: dict, who: str = "") -> dict:
        reading = sound_mod.clean_reading(raw)
        fire = []
        with self.lock:
            now = time.monotonic()
            self._sound = reading
            self._sound_at = now
            # how loud the room has been over the last ~8 s (the autopilot)
            prev = self.__dict__.get("_sound_energy")
            self._sound_energy = reading["level"] if prev is None else prev * 0.995 + reading["level"] * 0.005
            self._sound_from = who[:40] or None
            cfg = self._sound_cfg()
            t = self._tempo()
            if reading["beat"]:
                self._sound_beat_at = now
                if cfg["tempo"] and not (t.live(now) and t.source in ("midi", "prodj")):
                    # the room is the tempo source: each beat puts the phase
                    # right, the estimate sets the tempo
                    if reading["bpm"] and reading["confidence"] >= 0.5 and abs(reading["bpm"] - t.bpm) > 0.5:
                        t.set_bpm(reading["bpm"], now, "audio")
                        self._tempo_changed()
                    t.align_beat(now)
                    t.source, t.heard_at = "audio", now
            counts = self.__dict__.setdefault("_sound_counts", {})
            events = []
            if reading["beat"]:
                events.append("beat")
                if t.beat_in_bar(now) == 1:
                    events.append("bar")
            if reading["drop"]:
                events.append("drop")
            for tr in cfg["triggers"]:
                if not tr.get("enabled", True) or tr["on"] not in events:
                    continue
                counts[tr["id"]] = counts.get(tr["id"], 0) + 1
                if (counts[tr["id"]] - 1) % tr["every"] == 0:
                    fire.append(tr["button"])
        if reading["drop"]:
            self.ap_drop()
        for bid in fire:
            # a trigger is a quick press: down now, up a moment later (a
            # flash flashes, a latch toggles once)
            self.act("quick_press", id=bid, down=True)
            threading.Timer(0.15, lambda b=bid: self.act("quick_press", id=b, down=False)).start()
        return {"ok": True, "fired": fire}

    def _sound_overrides(self, out: dict) -> None:
        """Brightness from the links, as a per-head scale on the override."""
        cfg = self.__dict__.get("sound_cfg")
        if not cfg or not cfg["links"]:
            self._sound_speed = 1.0
            return
        now = time.monotonic()
        reading = self._sound_live(now)
        lights = [h["head_no"] for h in self.patch if self._head_class(h) == "light"]
        scales, speed = sound_mod.apply(cfg, reading, self.__dict__.get("_sound_beat_at"), now,
                                        lights, {g["n"]: g for g in self.groups})
        self._sound_speed = speed
        for n, f in scales.items():
            o = out.setdefault(n, {})
            o["scale"] = round((o.get("scale", 100) * f), 1)

    # -- actions: the links and triggers are part of the show --------------
    def _a_sound_link(self, link=None, id=None, remove=False, **_):
        """Add or change a sound link: {source, target {type, group|heads},
        depth, gain, on}; remove=true takes it away."""
        cfg = self._sound_cfg()
        if remove:
            before = len(cfg["links"])
            cfg["links"] = [lk for lk in cfg["links"] if lk["id"] != str(id)]
            if len(cfg["links"]) == before:
                raise ValueError(f"no sound link {id!r}")
            return {"sound": self.sound_public(), "summary": "sound link removed"}
        if not isinstance(link, dict):
            raise ValueError("link is {source, target, depth, gain}")
        ident = str(id or link.get("id") or "")
        if not ident:
            nums = [int(lk["id"][1:]) for lk in cfg["links"] if lk["id"][1:].isdigit()]
            ident = f"s{max(nums, default=0) + 1}"
            if len(cfg["links"]) >= sound_mod.MAX_LINKS:
                raise ValueError(f"at most {sound_mod.MAX_LINKS} sound links")
        clean = sound_mod.clean_link(link, ident)
        if clean["target"]["type"] == "group" and not any(g["n"] == clean["target"]["group"] for g in self.groups):
            raise ValueError(f"no group {clean['target']['group']}")
        cfg["links"] = [lk for lk in cfg["links"] if lk["id"] != ident] + [clean]
        what = {"master": "everything", "group": f"group {clean['target'].get('group')}",
                "heads": f"{len(clean['target'].get('heads') or [])} light(s)", "fx_speed": "effect speed"}[clean["target"]["type"]]
        return {"sound": self.sound_public(), "id": ident,
                "summary": f"{clean['source']} moves {what} ({clean['depth']}%)"}

    def _a_sound_trigger(self, trigger=None, id=None, remove=False, **_):
        """On a beat / the first beat of a bar / a drop, press a button
        (every Nth time); remove=true takes it away."""
        cfg = self._sound_cfg()
        if remove:
            before = len(cfg["triggers"])
            cfg["triggers"] = [t for t in cfg["triggers"] if t["id"] != str(id)]
            if len(cfg["triggers"]) == before:
                raise ValueError(f"no sound trigger {id!r}")
            return {"sound": self.sound_public(), "summary": "sound trigger removed"}
        if not isinstance(trigger, dict):
            raise ValueError("trigger is {on: beat|bar|drop, button, every}")
        ident = str(id or trigger.get("id") or "")
        if not ident:
            nums = [int(t["id"][1:]) for t in cfg["triggers"] if t["id"][1:].isdigit()]
            ident = f"t{max(nums, default=0) + 1}"
            if len(cfg["triggers"]) >= sound_mod.MAX_TRIGGERS:
                raise ValueError(f"at most {sound_mod.MAX_TRIGGERS} sound triggers")
        clean = sound_mod.clean_trigger(trigger, ident)
        if not any(b["id"] == clean["button"] for b in self.quick):
            raise ValueError(f"no button {clean['button']!r}")
        cfg["triggers"] = [t for t in cfg["triggers"] if t["id"] != ident] + [clean]
        every = "" if clean["every"] == 1 else f" every {clean['every']}"
        return {"sound": self.sound_public(), "id": ident,
                "summary": f"on each {clean['on']}{every}: button {clean['button']}"}

    def _a_sound_tempo(self, state=None, **_):
        """The room's beat sets the tempo (when no MIDI clock or CDJ is)."""
        cfg = self._sound_cfg()
        cfg["tempo"] = (not cfg["tempo"]) if state is None else str(state).lower() in ("1", "true", "on", "yes")
        return {"sound": self.sound_public(),
                "summary": "the room sets the tempo" if cfg["tempo"] else "the room no longer sets the tempo"}
