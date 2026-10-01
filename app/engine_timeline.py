"""The timeline: the engine owns the clock.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import json
import threading

from app import timeline as tl_mod
from app import venue as venue_mod
from app.engine_base import _truthy


class TimelineMixin:
    # ------------------------------------------------------------------
    # the timeline (app/timeline.py): the engine owns the clock
    # ------------------------------------------------------------------
    TIMELINE_TICK = 0.02

    def _tl_now(self) -> float:
        st = self.tl
        if not st["playing"]:
            return st["pos"]
        return st["pos0"] + (self._clock() - st["t0"])

    def _timeline_public(self) -> dict:
        return {**self.timeline, "transport": self._tl_transport()}

    def _tl_transport(self) -> dict:
        return {"timecode": self.timecode_public(),
                "playing": self.tl["playing"],
                "pos": round(min(self._tl_now(), self.timeline["length"]), 3),
                "length": self.timeline["length"], "loop": self.timeline["loop"]}

    def _tl_set_doc(self, doc: dict) -> None:
        self.timeline = tl_mod.normalise(doc)

    def _a_timeline_set(self, timeline=None, length=None, bpm=None, loop=None,
                        audio=None, markers=None, clear_audio=False, **_):
        """Replace the timeline, or change its length, tempo, loop, audio
        or markers."""
        doc = dict(timeline) if isinstance(timeline, dict) else dict(self.timeline)
        for key, val in (("length", length), ("bpm", bpm), ("loop", loop),
                         ("audio", audio), ("markers", markers)):
            if val is not None:
                doc[key] = _truthy(val) if key == "loop" else val
        if _truthy(clear_audio):
            doc["audio"] = None
        self._tl_set_doc(doc)
        t = self.timeline
        return {"timeline": self._timeline_public(),
                "summary": f"timeline {t['length']:g} s at {t['bpm']:g} BPM"
                           + (", looping" if t["loop"] else "")}

    def _a_timeline_track(self, id=None, remove=False, **fields):
        """Add a track ({kind, name, playback|target}), change one, or
        remove it (remove=true)."""
        fields = {k: v for k, v in fields.items() if not k.startswith("_")}
        if id and _truthy(remove):
            before = len(self.timeline["tracks"])
            self._tl_release_spans()
            self.timeline["tracks"] = [t for t in self.timeline["tracks"] if t["id"] != id]
            if len(self.timeline["tracks"]) == before:
                raise ValueError(f"no track {id}")
            return {"summary": f"removed track {id}"}
        if id:
            doc = tl_mod.normalise(json.loads(json.dumps(self.timeline)))
            t = tl_mod.track(doc, id)
            if not t:
                raise ValueError(f"no track {id}")
            merged = {**t, **{k: v for k, v in fields.items() if k not in ("id", "kind", "clips")}}
            clean = tl_mod.clean_track(merged, doc)
            doc["tracks"] = [clean if x["id"] == id else x for x in doc["tracks"]]
            self._tl_set_doc(doc)
            return {"track": clean, "summary": f"track {clean['name']}"}
        doc, t = tl_mod.with_track(self.timeline, fields)
        self._tl_set_doc(doc)
        return {"track": t, "id": t["id"], "summary": f"added {t['kind']} track {t['name']}"}

    def _a_timeline_clip(self, track=None, id=None, remove=False, **fields):
        """Add a clip to a track, change one (move, resize, retarget) or
        remove it."""
        fields = {k: v for k, v in fields.items() if not k.startswith("_")}
        doc = tl_mod.normalise(json.loads(json.dumps(self.timeline)))
        if id:
            found = tl_mod.find_clip(doc, str(id))
            if not found:
                raise ValueError(f"no clip {id}")
            t, c = found
            if _truthy(remove):
                t["clips"] = [x for x in t["clips"] if x["id"] != c["id"]]
                self._tl_release_spans()
                self._tl_set_doc(doc)
                return {"summary": "clip removed"}
            clean = tl_mod.clean_clip(t["kind"], {**c, **fields, "id": c["id"]}, doc)
            if not clean:
                raise ValueError("that change would leave the clip invalid")
            t["clips"] = sorted([clean if x["id"] == c["id"] else x for x in t["clips"]],
                                key=lambda x: x["t"])
            self._tl_set_doc(doc)
            return {"clip": clean, "summary": f"clip at {clean['t']:.2f} s"}
        t = tl_mod.track(doc, str(track or ""))
        if not t:
            raise ValueError(f"no track {track}")
        fields.pop("id", None)
        clean = tl_mod.clean_clip(t["kind"], fields, doc)
        if not clean:
            raise ValueError({"cue": "a cue clip needs a time",
                              "button": "a button clip needs a button",
                              "fx": "an effect clip needs an effect",
                              "level": "a level key needs a time"}[t["kind"]])
        t["clips"] = sorted(t["clips"] + [clean], key=lambda x: x["t"])
        doc["length"] = max(doc["length"], clean["t"] + clean.get("dur", 0) + 1)
        self._tl_set_doc(doc)
        return {"clip": clean, "id": clean["id"],
                "summary": f"added a clip at {clean['t']:.2f} s"}

    def _a_timeline_build(self, bars=16, start=None, before=None, heads=None, **_):
        """A build-up on the timeline ("a 32-bar build-up"): a dimmer chase
        that doubles its speed every quarter of it, pulsing in sixteenths in
        the last bar, the grand master climbing from half to full.  Ends at
        the drop (or the part named in `before`), else starts at `start` /
        the playhead."""
        doc = tl_mod.normalise(json.loads(json.dumps(self.timeline)))
        bars = int(max(1, min(256, float(bars or 16))))
        bar_s = 240.0 / float(doc.get("bpm") or 120.0)          # 4/4
        length = bars * bar_s
        if start not in (None, ""):
            t0 = max(0.0, float(start))
        else:
            ahead = tl_mod.section_span(doc, before or "drop")
            if ahead is not None:
                t0 = max(0.0, ahead[0] - length)
            elif before:
                raise ValueError(f"no {before!r} on the timeline - put a marker called {before} where it starts")
            else:
                t0 = float(self._tl_transport()["pos"])
        t1 = t0 + length
        nums = [int(n) for n in heads] if heads else [h["head_no"] for h in self.patch if self._head_class(h) == "light"]
        if not nums:
            raise ValueError("no lights are patched")
        target = {"heads": sorted(nums)} if heads else {"all": True}
        track = {"kind": "fx", "name": f"Copilot build {bars} bars", "clips": []}
        body = max(0, bars - 1)                                   # the last bar pulses
        cuts = [round(body * i / 4) for i in range(5)]
        for i, beats in enumerate((8, 4, 2, 1)):
            a, b = cuts[i], cuts[i + 1]
            if b > a:
                track["clips"].append({"t": round(t0 + a * bar_s, 3), "dur": round((b - a) * bar_s, 3),
                                       "fx": "dimmer_chase", "beats": beats, "target": target,
                                       "label": f"Chase, a round per {beats} beat(s)"})
        track["clips"].append({"t": round(t0 + body * bar_s, 3), "dur": round(bar_s, 3), "fx": "pulse",
                               "beats": 0.25, "target": target, "label": "Pulse in 16ths"})
        doc["tracks"].append(track)
        note = ""
        if any(t["kind"] == "level" and t.get("target") == "master" for t in doc["tracks"]):
            note = " (the master already has a level track - left as it is)"
        else:
            doc["tracks"].append({"kind": "level", "name": "Copilot build master", "target": "master",
                                  "clips": ([{"t": round(t0 - 0.05, 3), "v": 100}] if t0 > 0.05 else [])
                                  + [{"t": round(t0, 3), "v": 50}, {"t": round(t1, 3), "v": 100}]})
        doc["length"] = max(float(doc["length"]), t1 + 1)
        self._tl_set_doc(doc)
        return {"start": round(t0, 3), "end": round(t1, 3),
                "summary": f"{bars}-bar build-up, {t0:.1f}-{t1:.1f} s on the timeline{note}"}

    def _a_timeline_from_playback(self, playback=1, start=0.0, **_):
        """Lay a playback's cue list out on a new cue track, one clip per
        cue, spaced by each cue's fade, hold and follow."""
        pb = self._playback(playback)
        if not pb["stack"]:
            raise ValueError(f"playback {pb['n']} has no cues")
        doc, t = tl_mod.with_track(self.timeline, {
            "kind": "cue", "name": pb.get("name") or f"PB{pb['n']}",
            "playback": pb["n"],
            "clips": tl_mod.clips_from_stack(pb["stack"], float(start or 0))})
        doc["length"] = max(doc["length"], tl_mod.end_time(doc) + 4)
        self._tl_set_doc(doc)
        return {"track": t, "id": t["id"],
                "summary": f"{len(t['clips'])} cues from PB{pb['n']} on the timeline"}

    # -- transport -------------------------------------------------------
    def _a_timeline_play(self, at=None, **_):
        st = self.tl
        if at is not None:
            self._a_timeline_seek(t=at)
        if not st["playing"]:
            now = self._clock()
            if st["pos"] >= self.timeline["length"] - 1e-3:
                st["pos"] = 0.0
            st.update({"playing": True, "t0": now, "pos0": st["pos"],
                       "last": st["pos"] - 1e-6})
            self._ensure_tl_thread()
        return {"transport": self._tl_transport(), "summary": "timeline playing"}

    def _a_timeline_pause(self, **_):
        st = self.tl
        if st["playing"]:
            st["pos"] = min(self._tl_now(), self.timeline["length"])
            st["playing"] = False
            self._tl_release_spans()
        return {"transport": self._tl_transport(), "summary": "timeline paused"}

    def _a_timeline_stop(self, **_):
        self._a_timeline_pause()
        self.tl["pos"] = 0.0
        return {"transport": self._tl_transport(), "summary": "timeline stopped"}

    def _a_timeline_seek(self, t=None, chase=True, **_):
        """Jump the playhead; the rig is put where it would be at `t`."""
        if t is None:
            raise ValueError("t is required")
        pos = max(0.0, min(float(t), self.timeline["length"]))
        st = self.tl
        now = self._clock()
        self._tl_release_spans()
        st.update({"pos": pos, "pos0": pos, "t0": now, "last": pos})
        if _truthy(chase):
            for tr in self.timeline["tracks"]:
                if tr["kind"] == "cue" and not tr["mute"]:
                    c = tl_mod.last_cue_before(tr, pos)
                    if c and c["cue"] != "next":
                        try:
                            self._a_cue_go(playback=tr["playback"], cue=c["cue"])
                        except ValueError:
                            pass
            self._tl_spans(pos)
            self._tl_levels(pos)
        return {"transport": self._tl_transport(), "summary": f"playhead at {pos:.2f} s"}

    # -- firing ----------------------------------------------------------
    def _tl_release_spans(self) -> None:
        for cid, span in list(self.tl["spans"].items()):
            if span.get("button"):
                self._quick_off(span["button"], owner=cid)
            if span.get("fx"):
                self.fx = [f for f in self.fx if f["id"] != span["fx"]]
            del self.tl["spans"][cid]

    def _tl_fire(self, a: float, b: float) -> bool:
        """Point events with a < t <= b: cue GOs and one-shot buttons."""
        fired = False
        by_id = {x["id"]: x for x in self.quick}
        for tr in self.timeline["tracks"]:
            if tr["mute"]:
                continue
            for c in tr["clips"]:
                if not (a < c["t"] <= b):
                    continue
                if tr["kind"] == "cue":
                    try:
                        if c["cue"] == "next":
                            self._a_cue_go(playback=tr["playback"])
                        else:
                            self._a_cue_go(playback=tr["playback"], cue=c["cue"])
                        fired = True
                    except ValueError:
                        pass
                elif tr["kind"] == "button":
                    btn = by_id.get(c["button"])
                    if btn and btn["kind"] in ("go", "release", "preset"):
                        try:
                            self._a_quick_press(id=btn["id"], down=True)
                            fired = True
                        except ValueError:
                            pass
        return fired

    def _tl_spans(self, pos: float) -> bool:
        """Start the button/effect clips the playhead is inside, stop the
        ones it has left."""
        want = {}
        by_id = {x["id"]: x for x in self.quick}
        for tr in self.timeline["tracks"]:
            if tr["mute"] or tr["kind"] not in ("button", "fx"):
                continue
            for c in tl_mod.spans_at(tr, pos):
                if tr["kind"] == "button":
                    btn = by_id.get(c["button"])
                    if btn and btn["kind"] not in ("go", "release", "preset"):
                        want[c["id"]] = ("button", c)
                else:
                    want[c["id"]] = ("fx", c)
        changed = False
        for cid in [k for k in self.tl["spans"] if k not in want]:
            span = self.tl["spans"].pop(cid)
            if span.get("button"):
                self._quick_off(span["button"], owner=cid)
            if span.get("fx"):
                self.fx = [f for f in self.fx if f["id"] != span["fx"]]
            changed = True
        for cid, (kind, c) in want.items():
            if cid in self.tl["spans"]:
                continue
            if kind == "button":
                self._quick_on(c["button"], owner=cid)
                self.tl["spans"][cid] = {"button": c["button"]}
            else:
                heads = self._heads_for_target(c.get("target") or {"all": True})
                try:
                    if c.get("step"):
                        r = self._a_step_fx_run(id=c["step"], heads=heads or None, beats=c.get("beats"))
                    else:
                        r = self._a_run_fx(name=c["fx"], heads=heads,
                                           params={"beats": c["beats"]} if c.get("beats") else None) if heads else {}
                except ValueError:
                    r = {}
                self._fx_live(r.get("fx"))
                self.tl["spans"][cid] = {"fx": r.get("fx")}
            changed = True
        return changed

    def _tl_levels(self, pos: float) -> None:
        for tr in self.timeline["tracks"]:
            if tr["mute"] or tr["kind"] != "level":
                continue
            v = tl_mod.level_at(tr, pos)
            if v is None:
                continue
            v = int(round(v))
            if tr["target"] == "master":
                self.master = v
            else:
                try:
                    pb = self._playback(int(tr["target"][2:]))
                except (ValueError, TypeError):
                    continue
                pb["level"] = v

    def _tick_timeline(self, now: float | None = None) -> None:
        with self.lock:
            st = self.tl
            if not st["playing"]:
                return
            now = self._clock() if now is None else now
            pos = st["pos0"] + (now - st["t0"])
            length = self.timeline["length"]
            fired = False
            if pos >= length:
                fired = self._tl_fire(st["last"], length)
                self._tl_release_spans()
                if self.timeline["loop"]:
                    pos = (pos - length) % max(length, 1e-3)
                    st.update({"pos0": pos, "t0": now, "last": -1e-6})
                else:
                    st.update({"playing": False, "pos": length, "last": length})
                    self.act_rev += 1
                    return
            fired = self._tl_fire(st["last"], pos) or fired
            spans = self._tl_spans(pos)
            self._tl_levels(pos)
            st["last"] = pos
            st["pos"] = pos
            if fired or spans:
                self.act_rev += 1

    def _tl_loop(self) -> None:
        while not self._tl_stop.wait(self.TIMELINE_TICK):
            if not self.tl["playing"]:
                break
            try:
                self._tick_timeline()
            except Exception as exc:            # never die silently
                self.output["last_error"] = f"timeline: {exc}"
        self._tl_thread = None

    def _ensure_tl_thread(self) -> None:
        if self._tl_thread is not None and self._tl_thread.is_alive():
            return
        self._tl_stop.clear()
        thread = threading.Thread(target=self._tl_loop, name="jarvis-timeline",
                                  daemon=True)
        self._tl_thread = thread
        thread.start()

    def _a_place_many(self, moves=None, rig=None, **_):
        """Move several heads at once (a dragged selection): one undo step.
        With `rig`, each head mounts on it at the point nearest where it
        was dropped - drop a row of lights on a truss and they hang there."""
        if not isinstance(moves, list) or not moves:
            raise ValueError("moves must be a list of {head, x, y, z}")
        target = venue_mod.rig(self.venue, str(rig)) if rig else None
        if rig and not target:
            raise ValueError(f"no rig {rig!r}")
        done = []
        for m in moves[:512]:
            if not isinstance(m, dict) or m.get("head") is None:
                continue
            if target:
                near = venue_mod.nearest_rig({"rigging": [target]}, float(m.get("x", 0)),
                                             float(m.get("y", 0)), float(m.get("z", 0)),
                                             reach=1e9)
                self._a_set_place(head=m["head"], rig=target["id"],
                                  t=near[1] if near else 0.5)
            else:
                self._a_set_place(head=m["head"], x=m.get("x"), y=m.get("y"),
                                  z=m.get("z"))
            done.append(int(m["head"]))
        return {"heads": done, "rig": target["id"] if target else None,
                "summary": f"moved {len(done)} light(s)"
                           + (f" onto {target['name'] or target['id']}" if target else "")}

    def _a_attach_heads(self, heads=None, head=None, rig=None,
                        spacing=None, stance=None, **_):
        """Hang (or stand) heads along one rig, spread evenly from its
        middle - the "put these on the front truss" gesture."""
        r = venue_mod.rig(self.venue, str(rig or ""))
        if not r:
            raise ValueError(f"no rig {rig!r}")
        rows = ([self._head(int(head))] if head is not None
                else (self._cmd_rows(heads) if heads
                      else self._require_selection()))
        others = [float(h["mount"]["t"]) for h in self.patch
                  if (h.get("mount") or {}).get("rig") == r["id"]
                  and h not in rows]
        gap = float(spacing) if spacing else (0.5 if r["kind"] == "pipe" else 0.7)
        side = stance if stance in ("hang", "stand") else None
        members = [x for x in venue_mod.normalise(self.venue)["rigging"]
                   if r.get("group") and x.get("group") == r["group"]]
        if len(members) > 1 and len(rows) > 1:
            # a shape (circle, frame...): the lights spread round all of it,
            # starting at the piece named
            k = next(i for i, x in enumerate(members) if x["id"] == r["id"])
            members = members[k:] + members[:k]
            n, m = len(rows), len(members)
            spots = []
            for i, mem in enumerate(members):
                want = n // m + (1 if i < n % m else 0)
                if not want:
                    continue
                taken = [float(h["mount"]["t"]) for h in self.patch
                         if (h.get("mount") or {}).get("rig") == mem["id"] and h not in rows]
                got = sorted(venue_mod.free_slots(mem, taken, want, gap))
                if len(got) < want:
                    raise ValueError(f"{r['name'] or r['id']} has no room for {n} more at {gap:g} m spacing")
                spots += [(mem["id"], t) for t in got]
            for h, (rid, t) in zip(rows, spots):
                h["mount"] = {"rig": rid, "t": t}
                if side:
                    h["stance"] = side
                else:
                    h.pop("stance", None)
            self._reflow_mounts()
            self.patch_rev += 1
            return {"heads": [h["head_no"] for h in rows], "rig": r["id"],
                    "summary": f"{len(rows)} light(s) round {r['name'] or r['id']}"}
        slots = venue_mod.free_slots(r, others, len(rows), gap)
        if len(slots) < len(rows):
            raise ValueError(f"{r['name'] or r['id']} has room for "
                             f"{len(slots)} more at {gap:g} m spacing")
        slots.sort()
        rows = sorted(rows, key=lambda h: h["x"])
        for h, t in zip(rows, slots):
            h["mount"] = {"rig": r["id"], "t": t}
            if side:
                h["stance"] = side
            else:
                h.pop("stance", None)
        self._reflow_mounts()
        self.patch_rev += 1
        return {"heads": [h["head_no"] for h in rows], "rig": r["id"],
                "summary": f"{len(rows)} light(s) on {r['name'] or r['id']}"}

    def _a_locate(self, **_):
        """Show the selection: full light, white/open colour, open gobo.

        Must produce a visible change on EVERY patched head, including
        ones with no dimmer channel (their shutter/strobe opens) and
        ones whose colour lives on a wheel/gobo wheel (slot 1 = open).
        """
        heads = self._require_selection(lights_only=True)
        no_light = []
        for h in heads:
            values = self._level_values(h, 100)
            if not values:
                # No dimmer and no shutter: all this fixture can do is
                # point at an open colour/gobo slot, so light it there.
                values = {role: 0 for role in ("wheel", "gobo")
                          if role in h["map"]}
            if not values:
                no_light.append(h["head_no"])
                continue
            values.update(self._white_values(h))
            for role, v in values.items():
                self._set_programmer(h["head_no"], role, v)
        note = ""
        if no_light:
            note = f" ({len(no_light)} head(s) have no drivable channels)"
        return {"heads": len(heads) - len(no_light), "no_light": no_light,
                "summary": f"located {len(heads) - len(no_light)} head(s){note}"}

    def _a_clear_heads(self, heads=None, **_):
        """Drop the programmer's values from specific heads only.

        `clear_programmer` empties the lot, which is right for the CLEAR
        button and wrong for a line like `1-4 clear` - a console has to be
        able to release four heads out of a selection of twenty without
        losing the other sixteen, and there was no way to ask for that.
        """
        wanted = {int(h) for h in (heads or [])}
        if not wanted:
            raise ValueError("clear needs heads: `1-4 clear`")
        touched = []
        for head_no in sorted(wanted):
            row = self.programmer.get(head_no)
            if not row:
                continue
            touched.append({"head": head_no, "roles": sorted(row)})
            self.programmer.pop(head_no, None)
        if not touched:
            raise ValueError(
                "nothing to clear on head(s) "
                + ", ".join(str(h) for h in sorted(wanted)))
        return {"heads": len(touched), "cleared": touched,
                "summary": "cleared " + ", ".join(
                    f"{t['head']} ({len(t['roles'])})" for t in touched)}
