"""Show files, autosave, versions, Ready?, import and load.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app import pixels as pixels_mod
from app import fixture_kind
from app import timeline as tl_mod
from app import sound as sound_mod
from app import venue as venue_mod
from app.engine_base import (
    READY_ERROR_WINDOW_S,
    SAFE_NAME,
    _clamp,
    _normalize_playbacks,
    _palette_values,
    clean_dmx_target,
)
from app.engine_support import LASER_ROLES


class ShowMixin:
    # ------------------------------------------------------------------
    # show files
    # ------------------------------------------------------------------
    def _load_media_shapes(self, payload: dict) -> None:
        """Pictures and movement shapes from a saved show."""
        self.shapes = []
        for i, s in enumerate(payload.get("shapes") or [] if isinstance(payload.get("shapes"), list) else []):
            try:
                self.shapes.append(self._shape_clean(s, str((s or {}).get("id") or f"s{i + 1}")[:16]))
            except (ValueError, TypeError, AttributeError):
                continue
        self.shapes = self.shapes[:self.MAX_SHAPES]
        self.media = {}
        for mid, m in (payload.get("media") or {}).items() if isinstance(payload.get("media"), dict) else []:
            try:
                if m.get("kind") == "video":
                    self.media[str(mid)[:16]] = {"name": str(m.get("name") or "video")[:40], "kind": "video"}
                else:
                    w, h, _raw = pixels_mod.clean_media(m.get("w"), m.get("h"), m.get("data"))
                    self.media[str(mid)[:16]] = {"name": str(m.get("name") or "picture")[:40], "kind": "image",
                                                 "w": w, "h": h, "data": m["data"]}
            except (ValueError, TypeError, AttributeError, KeyError):
                continue

    def _safe_name(self, name) -> str:
        text = str(name or "").strip()
        if not SAFE_NAME.match(text):
            raise ValueError("show names use letters, digits, space, - and _ "
                             "(max 40 characters)")
        return text

    def _show_names(self) -> list[str]:
        try:
            return sorted(p.stem for p in self.show_dir.glob("*.json")
                          if not p.name.startswith("."))   # no dotfiles
        except OSError:
            return []

    # -- autosave (progress survives a crash; save_show stays a user act) --
    AUTOSAVE_MIN_INTERVAL = 1.0          # seconds between throttled writes

    def _autosave_payload(self) -> str:
        """Serialise the whole console state. Caller holds the lock.

        Pure serialisation, no I/O, so the background writer can own the
        disk without ever blocking the output thread.
        """
        payload = {
            "version": 1,
            "saved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "patch": self.patch,
            "groups": self.groups,
            "palettes": self.palettes,
            "presets": self.presets,
            "playbacks": [self._pb_saved(pb) for pb in self.playbacks],
            "programmer": {str(k): dict(v)
                           for k, v in self.programmer.items()},
            "selected": list(self.selected),
            "venue": self.venue,
            "quick": self.quick,
            "sound": self._sound_cfg(),
            "step_fx": self._steps(),
            "parked": self.__dict__.get("parked") or {},
            "macros": self._macros(),
            "moves": self.moves,
            "timeline": self.timeline,
            "output_target": self.dmx_target,
            "meta": {"master": self.master,
                     "show_file": self.show_file, **self._tempo_saved()},
        }
        return json.dumps(payload, indent=2)

    def _autosave(self, force: bool = False) -> bool:
        """Queue an autosave; a background writer owns the disk.

        Measured: serialising + writing a 120-head rig costs ~3 ms, which
        is 12% of a 40 Hz frame budget.  Doing that under the engine lock
        meant a visible hitch once a second, in the middle of a fade.  Now
        the serialisation happens under the lock (a few hundred us, and
        the data must be a consistent snapshot) and the write happens on
        the writer thread.
        """
        if self.autosave_path is None:
            return False
        now = time.monotonic()
        if not force and (now - self._autosave_at) < self.AUTOSAVE_MIN_INTERVAL:
            # Throttled, not dropped: the writer thread saves the trailing
            # edit once the interval has passed, so a crash a moment after
            # the last change cannot lose it.
            self._autosave_dirty = True
            return False
        self._autosave_dirty = False
        with self.lock:
            try:
                text = self._autosave_payload()
            except (TypeError, ValueError):
                return False
            path = self.autosave_path
        if not self._autosave_queue(text, path, force=force):
            return False
        self._autosave_at = now
        return True

    def _autosave_queue(self, text: str, path: Path,
                        force: bool = False) -> bool:
        """Hand the serialised state to the writer (or write it inline)."""
        if self._writer is None or not self._writer.is_alive():
            # No writer yet (tests, or shutdown): write inline so the file
            # still exists.  This is the one path that blocks, and it is
            # never on the output thread.
            return self._write_autosave(text, path)
        self._writer_pending = text
        self._writer_event.set()
        if force:
            # The caller wants it on disk NOW (shutdown): wait for the
            # writer to catch up rather than returning a lie.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                with self._writer_lock:
                    if self._writer_pending is None:
                        return True
                time.sleep(0.005)
            return self._write_autosave(text, path)
        return True

    @staticmethod
    def _write_autosave(text: str, path: Path) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError:
            return False

    def _autosave_writer(self) -> None:
        """Background writer: one deep queue, newest state wins."""
        while not self._writer_stop.is_set():
            self._writer_event.wait(0.25)
            self._writer_event.clear()
            if (self._autosave_dirty and time.monotonic() - self._autosave_at
                    >= self.AUTOSAVE_MIN_INTERVAL):
                self._autosave()
            while not self._writer_stop.is_set():
                with self._writer_lock:
                    text, path = self._writer_pending, self._writer_path
                    self._writer_pending = None
                if text is None or path is None:
                    break
                self._write_autosave(text, path)

    def _start_writer(self) -> None:
        if self._writer is not None or self.autosave_path is None:
            return
        self._writer_path = self.autosave_path
        self._writer_stop.clear()
        self._writer = threading.Thread(target=self._autosave_writer,
                                       name="jarvis-autosave", daemon=True)
        self._writer.start()

    def _stop_writer(self) -> None:
        writer = self._writer
        if writer is None:
            return
        self._writer_stop.set()
        self._writer_event.set()
        writer.join(timeout=3.0)
        self._writer = None
        # Anything still queued gets one final synchronous write, so a
        # shutdown never loses the last change.
        with self._writer_lock:
            text, path = self._writer_pending, self._writer_path
            self._writer_pending = None
        if text is not None and path is not None:
            self._write_autosave(text, path)

    @staticmethod
    def _clean_payload(payload) -> dict:
        """A show / autosave read from disk, made the right shape.

        The file is trusted for its values, never for its shape: a show
        cut short, a hand edit or a file from another program must be
        refused or tidied - never loaded half-way into a desk that then
        fails every frame.  Lists keep only their records, records that
        must be records are, and a group's lights are light numbers.
        """
        if not isinstance(payload, dict):
            raise ValueError("this file is not a show")
        out = dict(payload)
        for key in ("patch", "groups", "presets", "playbacks", "quick", "moves"):
            v = payload.get(key)
            out[key] = [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []
        for key in ("meta", "palettes", "quick_names", "programmer"):
            if not isinstance(payload.get(key), dict):
                out[key] = {}
        out["palettes"] = {k: v for k, v in out["palettes"].items() if isinstance(v, list)}
        groups, taken = [], set()
        for g in out["groups"]:
            heads = g.get("heads") if isinstance(g.get("heads"), list) else []
            n = g.get("n")
            if not isinstance(n, int) or n < 1 or n in taken:
                n = max(taken | {0}) + 1               # a group's number, unique
            taken.add(n)
            groups.append(dict(g, n=n, name=str(g.get("name") or f"Group {n}")[:60],
                               heads=[int(h) for h in heads if isinstance(h, (int, float)) or str(h).isdigit()]))
        out["groups"] = groups
        out["selected"] = payload.get("selected") if isinstance(payload.get("selected"), list) else []
        for key in ("timeline", "venue"):
            if not isinstance(payload.get(key), dict):
                out.pop(key, None)
        return out

    def _restore_autosave(self) -> bool:
        """Load the autosaved progress on boot.  Silent no-op when absent.

        Never stops the desk from starting: an autosave that can't be read
        (cut short by a power cut, garbage) is kept beside it as
        <name>.broken.json for a look later, and the desk starts empty."""
        if self.autosave_path is None or not self.autosave_path.is_file():
            return False
        before = self._undo_state()
        try:
            payload = self._clean_payload(json.loads(self.autosave_path.read_text(encoding="utf-8")))
            self._restore_payload(payload)
        except Exception as exc:              # noqa: BLE001 - boot must go on
            self._restore_state(before)
            try:
                self.autosave_path.replace(self.autosave_path.with_suffix(".broken.json"))
            except OSError:
                pass
            self._log("restore_autosave", False, f"the autosave could not be read and was kept aside: {exc}"[:200])
            return False
        return True

    def _restore_payload(self, payload: dict) -> None:
        """The autosave's show onto this (empty, booting) desk."""
        heads = [self._head_from_layout(h)
                 for h in payload.get("patch") or []]
        self._replace_patch(heads)
        self.groups = list(payload.get("groups") or [])
        saved_palettes = self._normalize_palettes(payload.get("palettes"))
        for key in self.palettes:
            self.palettes[key] = list(saved_palettes.get(key) or [])
        self.presets = [dict(q) for q in (payload.get("presets") or [])
                        if isinstance(q, dict)]
        self.playbacks = _normalize_playbacks(
            payload.get("playbacks") or [])
        self._resync_cue_fx()
        patched = {h["head_no"] for h in self.patch}
        self.programmer = {}
        for k, row in (payload.get("programmer") or {}).items():
            try:
                head_no = int(k)
            except (TypeError, ValueError):
                continue
            if head_no in patched and isinstance(row, dict):
                # 0-65535: 16-bit parameters (pan/tilt fine pairs)
                # must survive a restart unchanged.
                self.programmer[head_no] = {
                    str(role): _clamp(v, 0, 65535)
                    for role, v in row.items()}
        self.selected = [n for n in (int(x) for x in
                                     payload.get("selected") or [])
                         if n in patched]
        meta = payload.get("meta") or {}
        self.master = _clamp(meta.get("master", 100), 0, 100)
        self._tempo_restore(meta)
        self.show_file = meta.get("show_file") or self.show_file
        self.timeline = tl_mod.normalise(payload.get("timeline") or {})
        self.sound_cfg = sound_mod.clean_config(payload.get("sound"))
        self.step_fx = self._clean_step_list(payload.get("step_fx"))
        self._load_media_shapes(payload)
        self.parked = self._clean_parked(payload.get("parked"))
        self.macros = self._clean_macro_list(payload.get("macros"))
        if isinstance(payload.get("output_target"), dict):
            self.dmx_target = clean_dmx_target(payload["output_target"])
        for b in payload.get("quick") or []:
            try:
                self.quick.append(self._quick_clean(b, int(b["page"]), int(b["slot"])))
            except (KeyError, TypeError, ValueError):
                continue
        venue = payload.get("venue")
        if isinstance(venue, dict):
            try:
                self.venue = venue_mod.normalise(venue)
            except (ValueError, TypeError):
                self.venue = venue_mod.empty()  # never block boot

    def _a_save_show(self, name="", **_):
        label = self._safe_name(name or "show")
        # Snapshot + serialise under the lock, write the file OUTSIDE it:
        # json.dumps of a 120-head rig is ~3 ms, which is 12% of a 40 Hz
        # frame budget, and SAVE SHOW is a user action that can land in
        # the middle of a fade.
        with self.lock:
            payload = {
                "version": 1,
                "saved": datetime.now(timezone.utc)
                .isoformat(timespec="seconds"),
                "patch": json.loads(json.dumps(self.patch, default=str)),
                "groups": json.loads(json.dumps(self.groups, default=str)),
                "palettes": json.loads(json.dumps(self.palettes,
                                                   default=str)),
                "presets": json.loads(json.dumps(self.presets, default=str)),
                "playbacks": [self._pb_saved(pb) for pb in self.playbacks],
                "venue": json.loads(json.dumps(self.venue, default=str)),
                "quick": json.loads(json.dumps(self.quick, default=str)),
                "quick_names": dict(getattr(self, "quick_names", {}) or {}),
                "quick_quant": float(self.__dict__.get("quick_quant", 0.0)),
                "media": json.loads(json.dumps(self._media(), default=str)),
                "shapes": json.loads(json.dumps(self._shapes(), default=str)),
                "sound": json.loads(json.dumps(self._sound_cfg(), default=str)),
                "step_fx": json.loads(json.dumps(self._steps(), default=str)),
                "parked": json.loads(json.dumps(self.__dict__.get("parked") or {}, default=str)),
                "macros": json.loads(json.dumps(self._macros(), default=str)),
                "moves": json.loads(json.dumps(self.moves, default=str)),
                "timeline": json.loads(json.dumps(self.timeline, default=str)),
                "output_target": dict(self.dmx_target),
                "meta": {"master": self.master, **self._tempo_saved()},
            }
            text = json.dumps(payload, indent=2)
            self.show_file = label
        self.show_dir.mkdir(parents=True, exist_ok=True)
        path = self.show_dir / f"{label}.json"
        kept = self._keep_version(path, text)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return {"file": label, "show_file": label, "version_kept": kept,
                "summary": f"saved show {label!r}"}

    def _a_rdm_compare(self, devices=None, universes=None, **_):
        """Line up what the lights said over RDM with the patch: each light
        is "ok" (a patched light at that address, same channel count),
        "different" (patched there, but the light says otherwise) or "new"
        (nothing patched there: + Add it).  Patched lights on the scanned
        universes that nobody answered for are listed as "silent"."""
        rows = []
        seen = set()
        by_addr = {(h["universe"], h["address"]): h for h in self.patch}
        for d in devices or []:
            u, a = int(d.get("universe") or 1), d.get("address")
            name = " ".join(x for x in (d.get("manufacturer"), d.get("model")) if x) or d.get("uid")
            row = {**d, "name": name}
            h = by_addr.get((u, a)) if a else None
            if h is None:
                row["status"] = "new"
                row["note"] = "not in the patch" + (f" - {d.get('footprint')} ch at {u}.{a}" if a else "")
            else:
                seen.add(h["head_no"])
                row["head"] = h["head_no"]
                fp = d.get("footprint")
                if fp and fp != h["channels"]:
                    row["status"] = "different"
                    row["note"] = f"#{h['head_no']} is patched as {h['model']} ({h['channels']} ch); the light says {fp} ch" \
                                  + (f" ({d['mode']})" if d.get("mode") else "")
                else:
                    row["status"] = "ok"
                    row["note"] = f"#{h['head_no']} {h['name']}"
            rows.append(row)
        scanned = {int(u) for u in universes or []}
        silent = [{"head": h["head_no"], "name": h["name"], "universe": h["universe"], "address": h["address"],
                   "status": "silent", "note": "didn't answer (no RDM, off, or a different address)"}
                  for h in self.patch if h["universe"] in scanned and h["head_no"] not in seen]
        n_bad = sum(r["status"] != "ok" for r in rows)
        return {"devices": rows, "silent": silent,
                "summary": f"{len(rows)} RDM light(s) answered" + (f", {n_bad} to check" if n_bad else "")}

    def _a_ready_check(self, **_):
        """Before doors: everything that would bite during the show, each
        with what to press.  Read-only."""
        items: list[dict] = []

        def add(level, text, fix=""):
            items.append({"level": level, "text": text, "fix": fix})

        if not self.patch:
            add("bad", "No lights are patched.", "+ Add")
        clashes = self._patch_clashes()
        if clashes:
            add("bad", f"{len(clashes)} DMX clash(es): two lights share channels.", "Fixtures → the red warning → Move")
        raw = [h["head_no"] for h in self.patch if not h.get("mapped", True)]
        if raw:
            add("warn", f"{len(raw)} light(s) have channels the desk can't name ({', '.join(f'#{n}' for n in raw[:6])}).",
                "Edit fixture profile")
        gone = self._stale_heads(self.patch, self.playbacks)
        if gone:
            add("warn", f"Cues point at {len(gone)} light(s) no longer patched.", "Re-patch them or update the cues")
        if not any(pb["stack"] for pb in self.playbacks):
            add("warn", "No cues recorded yet.", "Record a cue")
        always = [h["head_no"] for h in self.patch if self._lamp_only(h)]
        if always:
            add("warn", f"{len(always)} light(s) have no dimmer or shutter in their mode, so Blackout can't "
                        f"darken them ({', '.join(f'#{n}' for n in always[:6])}).",
                "Pick a mode with a dimmer / shutter, or fix the profile")
        movers = [h for h in self.patch if "pan" in h["map"] or "tilt" in h["map"]]
        if movers and not self.floor_safe:
            add("warn", f"Stay-on-the-floor is off for {len(movers)} moving light(s).", "Move tab → Stay on the floor")
        lasers = [h for h in self.patch if any(r in LASER_ROLES for r in h["map"])]
        if lasers:
            add("info", f"{len(lasers)} laser(s): output only while ARMED; KILL FX stops everything.", "")
        if self.dry_run or not self.live:
            add("warn", "BLIND / output stopped: nothing reaches the lights yet.", "Go live…")
        # the error count is for the whole run: one hiccup an hour ago is
        # history, only a recent failure means the output is broken now
        err_at = self.output.get("last_error_at")
        if self.output.get("errors") and err_at is not None and time.monotonic() - err_at < READY_ERROR_WINDOW_S:
            add("bad", f"{self.output['errors']} DMX send error(s): {self.output.get('last_error') or ''}".strip(),
                "Settings → Output")
        if not self.show_file:
            add("warn", "The show has never been saved.", "Show ▾ → Save")
        if not items or all(i["level"] == "info" for i in items):
            add("ok", "Ready: nothing to fix.")
        worst = next((lv for lv in ("bad", "warn") if any(i["level"] == lv for i in items)), "ok")
        return {"ready": worst == "ok", "worst": worst, "items": items,
                "summary": "ready" if worst == "ok" else
                f"{sum(i['level'] == worst for i in items)} thing(s) to check"}

    SHOW_VERSIONS = 20

    def _versions_dir(self, label: str) -> Path:
        return self.show_dir / "versions" / label

    def _keep_version(self, path: Path, new_text: str) -> bool:
        """Before a save overwrites a show, keep the old file (when it is
        different) as a dated version; the last SHOW_VERSIONS stay."""
        if not path.is_file():
            return False
        try:
            old_text = path.read_text(encoding="utf-8")
            strip = lambda t: {k: v for k, v in json.loads(t).items() if k != "saved"}  # noqa: E731
            if strip(old_text) == strip(new_text):
                return False
            stamp = str(json.loads(old_text).get("saved") or "")
        except (OSError, json.JSONDecodeError, AttributeError):
            old_text, stamp = path.read_bytes().decode("utf-8", "replace"), ""
        d = self._versions_dir(path.stem)
        d.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^0-9T]", "", stamp)[:15] or datetime.now().strftime("%Y%m%dT%H%M%S")
        dest = d / f"{name}.json"
        n = 1
        while dest.exists():
            n += 1
            dest = d / f"{name}-{n}.json"
        dest.write_text(old_text, encoding="utf-8")
        for extra in sorted(d.glob("*.json"))[:-self.SHOW_VERSIONS]:
            extra.unlink(missing_ok=True)
        return True

    def _a_show_versions(self, name="", **_):
        """The kept versions of a show, newest first."""
        label = self._safe_name(name or self.show_file or "show")
        d = self._versions_dir(label)
        rows = []
        for p in sorted(d.glob("*.json"), reverse=True) if d.is_dir() else []:
            try:
                saved = json.loads(p.read_text(encoding="utf-8")).get("saved")
            except (OSError, json.JSONDecodeError, AttributeError):
                saved = None
            rows.append({"id": p.stem, "saved": saved, "bytes": p.stat().st_size})
        return {"show": label, "versions": rows,
                "summary": f"{len(rows)} earlier version(s) of {label!r}"}

    def _a_restore_version(self, name="", id="", **_):
        """Open an earlier version of a show.  The show as it is now is kept
        as a version first, so restoring can itself be undone."""
        label = self._safe_name(name or self.show_file or "show")
        vid = str(id or "")
        if not re.fullmatch(r"[0-9T]+(-\d+)?", vid):
            raise ValueError("pick a version")
        src = self._versions_dir(label) / f"{vid}.json"
        if not src.is_file():
            raise ValueError(f"no version {vid} of {label!r}")
        path = self.show_dir / f"{label}.json"
        text = src.read_text(encoding="utf-8")
        self._keep_version(path, text)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        r = self._a_load_show(name=label)
        r["summary"] = f"opened the {vid[:8]} {vid[9:13]} version of {label!r} (the newer one is kept)"
        return r

    def _a_show_export(self, name="", **_):
        """The show file's text, for a download / a USB stick."""
        label = self._safe_name(name or self.show_file or "show")
        path = self.show_dir / f"{label}.json"
        if not path.is_file():
            raise ValueError(f"save the show first (no file {label!r})")
        return {"show": label, "filename": f"{label}.json", "text": path.read_text(encoding="utf-8"),
                "summary": f"exported {label!r}"}

    @staticmethod
    def _normalize_rows(values) -> dict[int, dict]:
        """Re-key a {head_no: {role: value}} map after a JSON round-trip.

        json.dumps turns every int head key into a string ("1" instead of
        1) while merge-time lookups (_pb_values, _resolve_head,
        _a_include_palette) index the dict with the INT head number - so
        anything restored from disk silently resolves to nothing.  Returns
        int keys with DMX values clamped to 0-65535 (16-bit pairs ride in
        the same rows).
        """
        out: dict[int, dict] = {}
        for key, row in (values or {}).items():
            try:
                head_no = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            clean = {}
            for role, value in row.items():
                try:
                    clean[str(role)] = _clamp(int(value), 0, 65535)
                except (TypeError, ValueError):
                    continue
            out[head_no] = clean
        return out

    @classmethod
    def _normalize_stack(cls, stack) -> list[dict]:
        """Repair cue stacks that survived a JSON round-trip.

        See _normalize_rows: the head keys must be ints again or the whole
        playback stack would drive nothing after a restart - the classic
        "playback does not work" report.
        """
        out: list[dict] = []
        for cue in stack or []:
            if not isinstance(cue, dict):
                continue
            entry = dict(cue)
            entry["n"] = int(cue.get("n") or len(out) + 1)
            entry["name"] = str(cue.get("name") or f"Cue {entry['n']}")
            entry["values"] = cls._normalize_rows(cue.get("values"))
            entry["fade_s"] = float(cue.get("fade_s") or 0.0)
            entry["hold_s"] = float(cue.get("hold_s") or 0.0)
            out.append(entry)
        return out

    @classmethod
    def _normalize_palettes(cls, palettes) -> dict:
        out = {}
        for key, entries in (palettes or {}).items():
            fixed = []
            for entry in entries or []:
                if not isinstance(entry, dict):
                    continue
                row = dict(entry)
                row["values"] = _palette_values(entry.get("values"))
                fixed.append(row)
            out[str(key)] = fixed
        return out

    def _tempo_saved(self) -> dict:
        """The show's tempo: a timeline built at 128 BPM came back at 120."""
        t = self.__dict__.get("tempo")
        if t is None:
            return {}
        return {"bpm": round(float(t.bpm), 3), "tempo_follow": bool(self.__dict__.get("tempo_follow", True))}

    def _tempo_restore(self, meta: dict) -> None:
        try:
            bpm = float(meta.get("bpm") or 0)
        except (TypeError, ValueError):
            return
        if not 20 <= bpm <= 400:
            return
        self._tempo().set_bpm(bpm, time.monotonic(), "show")
        self.tempo_follow = bool(meta.get("tempo_follow", True))
        self._tempo_changed()

    @staticmethod
    def _pb_saved(pb: dict) -> dict:
        f = pb["follow"]
        return {"n": pb["n"], "name": pb["name"], "stack": pb["stack"],
                "index": pb["index"], "active": pb["active"],
                "level": pb["level"],
                # The crossfade TIME, not the running fade.  Saving the
                # running one would reload a show mid-fade with a stale
                # start value, and saving nothing means the console forgets
                # it and the operator sets it again wondering why it was
                # there before.
                "xfade_s": (float(pb["xfade"]["dur"])
                            if pb.get("xfade") else None),
                "follow": {"on": f["on"], "delay": f["delay"],
                           "paused": f["paused"], "loop": f["loop"]},
                "tracking": bool(pb.get("tracking")), "mib": bool(pb.get("mib"))}

    @staticmethod
    def _stale_heads(heads: list[dict], playbacks: list[dict]) -> list[int]:
        """Heads a saved stack still references that are no longer patched.

        The rows stay in the cue (deleting them would be data loss, and they
        are inert at merge time - an unpatched head is simply never looked
        up), but the operator should be told, because a stack that is 100%
        stale is exactly the "playback does nothing" report.
        """
        patched = {h["head_no"] for h in heads}
        stale: set[int] = set()
        for pb in playbacks:
            for cue in pb.get("stack") or []:
                stale.update(int(n) for n in (cue.get("values") or {})
                             if int(n) not in patched)
        return sorted(stale)

    @staticmethod
    def _apply_follow(base: dict, saved: dict) -> None:
        """Restore follow config from a saved playback row.

        The deadline (`at`) is monotonic - process-local - so it always
        restarts as None; a restored follow re-arms on the next cue step
        or follow_set instead of firing against a stale timestamp.
        """
        row = (saved or {}).get("follow") or {}
        f = base["follow"]
        try:
            f["delay"] = max(0.0, float(row.get("delay") or 0.0))
        except (TypeError, ValueError):
            f["delay"] = 0.0
        f["on"] = bool(row.get("on"))
        f["paused"] = bool(row.get("paused"))
        f["loop"] = bool(row.get("loop"))
        f["at"] = None

    def _a_load_show(self, name="", **_):
        label = self._safe_name(name)
        path = self.show_dir / f"{label}.json"
        if not path.is_file():
            raise ValueError(f"no show file {label!r}")
        # Slow read phase, unlocked: the file read and the fixture-mode
        # resolution both touch the disk.  Nothing below mutates state
        # until the commit block.
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:          # ValueError: bad JSON or not text
            raise ValueError(f"cannot read show {label!r}: it is damaged ({exc})") from exc
        payload = self._clean_payload(payload)
        heads = [self._head_from_layout(h) for h in payload.get("patch") or []]
        groups = list(payload.get("groups") or [])
        palettes = self._normalize_palettes(payload.get("palettes"))
        presets = [dict(q) for q in (payload.get("presets") or [])
                   if isinstance(q, dict)]
        playbacks = _normalize_playbacks(payload.get("playbacks") or [])
        master = _clamp((payload.get("meta") or {}).get("master", 100), 0, 100)
        venue = (venue_mod.normalise(payload["venue"])
                 if isinstance(payload.get("venue"), dict) else None)
        quick = []
        for b in payload.get("quick") or []:
            try:
                quick.append(self._quick_clean(b, int(b["page"]), int(b["slot"])))
            except (KeyError, TypeError, ValueError):
                continue

        # Commit phase: swap the whole show in under the lock.  A bad patch
        # raises from _replace_patch, which rolls the patch back, and the
        # remaining state is untouched because we have not assigned it yet.
        with self.lock:
            self._replace_patch(heads)
            self.groups = groups
            for key in self.palettes:
                self.palettes[key] = list(palettes.get(key) or [])
            self.presets = presets
            self.playbacks = playbacks
            self._resync_cue_fx()
            self.master = master
            self._tempo_restore(payload.get("meta") or {})
            self.show_file = label
            self.quick = quick
            self.quick_names = {str(k): str(v)[:16] for k, v in
                                (payload.get("quick_names") or {}).items()} \
                if isinstance(payload.get("quick_names"), dict) else {}
            try:
                qq = float(payload.get("quick_quant") or 0.0)
            except (TypeError, ValueError):
                qq = 0.0
            self.quick_quant = qq if qq in self.QUANTS else 0.0
            self._load_media_shapes(payload)
            self.sound_cfg = sound_mod.clean_config(payload.get("sound"))
            self.step_fx = self._clean_step_list(payload.get("step_fx"))
            self.parked = self._clean_parked(payload.get("parked"))
            self.macros = self._clean_macro_list(payload.get("macros"))
            self.moves = []
            for m in payload.get("moves") or []:
                try:
                    self.moves.append(self._move_clean(m))
                except (KeyError, TypeError, ValueError):
                    continue
            self.quick_active = {}
            self._a_fx_kill()                  # a new show starts disarmed
            self._a_timeline_stop()
            self.timeline = tl_mod.normalise(payload.get("timeline") or {})
            if venue is not None:           # older shows kept no room
                self.venue = venue
            if isinstance(payload.get("output_target"), dict):
                # the show was saved at a venue: its node comes with it
                self.dmx_target = clean_dmx_target(payload["output_target"])
                self._reflow_mounts()
                self.patch_rev += 1
        return {"file": label, "heads": len(heads), "show_file": label,
                "stale_heads": self._stale_heads(heads, playbacks),
                "summary": f"loaded show {label!r} ({len(heads)} heads)"}

    def _a_import_show(self, concept=None, playback=None, name="", **_):
        """Turn a show-design concept into a playable cue stack."""
        if not isinstance(concept, dict):
            raise ValueError("concept object is required")
        data = concept
        if not data.get("cues") and data.get("concepts"):
            data = (data.get("concepts") or [{}])[0]
        cues = data.get("cues") or []
        if not cues:
            raise ValueError("concept has no cues")
        if not self.patch:
            raise ValueError("patch is empty - load a layout first")
        pb = self._playback(playback if playback is not None else 1)
        colours = data.get("colours") or {}
        intensity = data.get("intensity") or {}
        active = data.get("active")
        if isinstance(active, dict):
            active_roles = {k for k, v in active.items() if v}
        elif isinstance(active, list):
            active_roles = {str(r) for r in active}
        else:
            active_roles = set()

        # A concept names DESIGN roles (wash/beam/spot/...), but a patch
        # imported from CSV or hand-written can be all "generic".  When
        # the two vocabularies do not intersect, applying the concept
        # literally lights NOTHING - every cue would come out as
        # {"dimmer": 0} and playback would look broken.  So fall back to
        # "every head participates" (and take the level/colour from
        # whichever role the concept did specify).
        patch_roles = {fixture_kind.design_role(h) for h in self.patch}
        # role -> the head numbers it would drive, so the UI can offer
        # "assign heads 2-5 to wash" instead of leaving the operator to
        # guess why the spots came up the wrong colour.
        role_heads: dict[str, list[int]] = {}
        for head in self.patch:
            role_heads.setdefault(fixture_kind.design_role(head),
                                  []).append(head["head_no"])
        role_mismatch = False
        used_roles: set[str] = set()

        def level_for(table: dict, role: str) -> int:
            """Concept level for a role: role -> generic -> any declared."""
            if role in table:
                return _clamp(table[role], 0, 100)
            if "generic" in table:
                return _clamp(table["generic"], 0, 100)
            if table:
                for value in table.values():
                    return _clamp(value, 0, 100)
            return 0

        def colour_for(table: dict, role: str) -> str:
            return (table.get(role) or table.get("generic")
                    or next(iter(table.values()), "#ffffff"))

        stack = []
        for cue in cues:
            cue_active = cue.get("active")
            if isinstance(cue_active, dict):
                roles_on = {k for k, v in cue_active.items() if v}
            elif isinstance(cue_active, list):
                roles_on = {str(r) for r in cue_active}
            else:
                roles_on = active_roles or None
            if roles_on and not (roles_on & patch_roles):
                # Concept roles this patch has no heads for.  Light
                # everything (a black show is the worse failure) and
                # record what was asked for, so the result can name the
                # assumption and the UI can offer to assign roles.
                role_mismatch = True
                used_roles |= set(roles_on)
                roles_on = None
            cue_colours = cue.get("colours") or colours
            cue_intensity = cue.get("intensity") or intensity
            values = {}
            for head in self.patch:
                role = fixture_kind.design_role(head)
                on = roles_on is None or role in roles_on
                pct = level_for(cue_intensity, role) if on else 0
                hexcol = colour_for(cue_colours, role)
                row = {}
                if pct > 0:
                    try:
                        row.update(self._colour_values(head, hexcol))
                    except ValueError:
                        row.update(self._white_values(head))
                row.update(self._level_values(head, pct))
                values[head["head_no"]] = row
            stack.append({
                "n": len(stack) + 1,
                "name": str(cue.get("name") or f"Cue {len(stack) + 1}"),
                "fade_s": float(cue.get("fade_s") or 0.0),
                "hold_s": float(cue.get("hold_s") or 0.0),
                "values": values,
            })
        pb["stack"] = stack
        pb["index"] = -1
        pb["active"] = False
        pb["fade"] = None
        self._cue_fx_stop(pb)
        if name:
            pb["name"] = str(name)
        elif data.get("name"):
            pb["name"] = str(data["name"])
        self.patch_rev += 1                # notify lite clients
        note = " (patch roles unknown to the concept - all heads lit)" \
            if role_mismatch else ""
        result = {"playback": pb["n"], "cues": len(stack),
                  "name": pb["name"], "role_fallback": role_mismatch,
                  "summary": f"imported {len(stack)} cues into "
                             f"PB{pb['n']}{note}"}
        if role_mismatch:
            # Name the assumption and the fix: the concept wanted these
            # roles, the patch only has these.  The UI can offer to assign
            # the head ranges and re-import.
            result["concept_roles"] = sorted(used_roles)
            result["patch_roles"] = sorted(patch_roles)
            result["role_heads"] = {role: sorted(nums) for role, nums
                                    in sorted(role_heads.items())}
        return result
