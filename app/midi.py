"""MIDI input: device enumeration, note/CC events, configurable mappings.

Pure standard library.  On Windows the OS MIDI API (winmm) is reached
through ctypes; anywhere else - and whenever no device exists - the
manager degrades to a graceful "no device" status without raising, so
CI and machines without hardware always start.

Event flow (integrated with the EXISTING control/event architecture -
nothing here is hard-coded per fixture):

    device -> reader thread -> parse_short() -> MidiMapper.resolve()
          -> [(engine action, params), ...] -> engine.act(...)

Mappings are data, not code: a dict (DEFAULT_MAP) or a JSON file
(data/midi_map.json, or MIDI_MAP).  Notes map to actions/cues/scenes,
CCs map to parameters with $value / $pct / $on token substitution:

    {"notes": {"36": {"action": "cue_go", "params": {"playback": 1}}},
     "cc":    {"1":  {"action": "master", "params": {"level": "$pct"}}},
     "cc":    {"2":  {"action": "set_attribute",
                      "params": {"attribute": "pan", "value": "$value"}}},
     "steps"  form allows select -> set chains for fixture parameters}

Tests inject FakeMidiSource (a queue with the same shapes winmm
produces), so no physical MIDI hardware is ever needed.
"""
from __future__ import annotations

import collections
import json
import queue
import sys
import threading
import time
from pathlib import Path

from app.engine import ACTIONS

MIM_CLOSE = 0x3C2
MIM_DATA = 0x3C4
CALLBACK_FUNCTION = 0x00030000
MMSYSERR_NOERROR = 0

# Default mapping: 10 drum pads GO the cue stacks (notes 36-45 -> PB1-10),
# note 48 locates the selection, note 49 blackouts while held and CC 1
# (mod wheel) drives the grand master.  Everything else is user data.
DEFAULT_MAP = {
    "notes": {
        "36": {"action": "cue_go", "params": {"playback": 1}},
        "37": {"action": "cue_go", "params": {"playback": 2}},
        "38": {"action": "cue_go", "params": {"playback": 3}},
        "39": {"action": "cue_go", "params": {"playback": 4}},
        "40": {"action": "cue_go", "params": {"playback": 5}},
        "41": {"action": "cue_go", "params": {"playback": 6}},
        "42": {"action": "cue_go", "params": {"playback": 7}},
        "43": {"action": "cue_go", "params": {"playback": 8}},
        "44": {"action": "cue_go", "params": {"playback": 9}},
        "45": {"action": "cue_go", "params": {"playback": 10}},
        "48": {"action": "locate"},
        "49": {"action": "blackout", "params": {"state": "$on"}},
    },
    "cc": {
        "1": {"action": "master", "params": {"level": "$pct"}},
    },
}


def parse_short(status: int, data1: int, data2: int) -> dict | None:
    """One MIDI short message -> event dict, or None when unmappable.

    Channel voice messages only: note on/off (0x90/0x80) and control
    change (0xB0).  Everything else (pitch bend, sysex, clock, ...)
    returns None so the mapper never sees noise.
    """
    kind = status & 0xF0
    channel = (status & 0x0F) + 1
    number = data1 & 0x7F
    value = data2 & 0x7F
    if kind == 0x90:                       # note on (velocity 0 = release)
        return {"kind": "note", "channel": channel, "number": number,
                "value": value, "on": value > 0}
    if kind == 0x80:                       # note off
        return {"kind": "note", "channel": channel, "number": number,
                "value": 0, "on": False}
    if kind == 0xB0:                       # control change
        return {"kind": "cc", "channel": channel, "number": number,
                "value": value}
    return None


def _substitute(value, event: dict):
    """Replace $tokens in a mapping parameter with live event data."""
    if not isinstance(value, str) or "$" not in value:
        return value
    if value == "$value":
        return event["value"]
    if value == "$pct":
        return event["value"] * 100 // 127
    if value == "$on":
        if event["kind"] == "note":
            return 1 if event["on"] else 0
        return 1 if event["value"] > 0 else 0
    if value == "$number":
        return event["number"]
    if value == "$channel":
        return event["channel"]
    return value


class MidiMapper:
    """Validated mapping: event -> [(action, params), ...].

    Invalid entries (unknown action, malformed structure) are dropped at
    construction and listed in `skipped` instead of raising - one bad
    line in a hand-edited map must not kill MIDI input.
    """

    def __init__(self, mapping: dict | None = None):
        source = mapping if mapping is not None else DEFAULT_MAP
        if not isinstance(source, dict):
            raise ValueError("MIDI map must be a JSON object")
        self.notes: dict[str, dict] = {}
        self.ccs: dict[str, dict] = {}
        self.skipped: list[str] = []
        # absent or null tolerated, a WRONG type must be rejected -
        # `or {}` would silently swallow a hand-edited `"notes": []`.
        notes = source.get("notes")
        ccs = source.get("cc")
        notes = {} if notes is None else notes
        ccs = {} if ccs is None else ccs
        if not isinstance(notes, dict) or not isinstance(ccs, dict):
            raise ValueError("MIDI map needs 'notes' and 'cc' objects")
        for table, entries in (("note", notes), ("cc", ccs)):
            for key, entry in entries.items():
                name = f"{table} {key}"
                if not isinstance(key, str) or not key:
                    self.skipped.append(name)
                    continue
                valid = self._validate(entry, name)
                if valid is None:
                    self.skipped.append(name)
                    continue
                (self.notes if table == "note" else self.ccs)[key] = valid

    @staticmethod
    def _validate(entry, name: str):
        """Normalise one mapping entry to {steps:[{action,params}]}."""
        if not isinstance(entry, dict):
            return None
        if "steps" in entry:
            steps = entry.get("steps")
            if not isinstance(steps, list) or not steps:
                return None
            rows = steps
        else:
            rows = [entry]
        out = []
        for row in rows:
            if not isinstance(row, dict):
                return None
            action = row.get("action")
            if not isinstance(action, str) or action not in ACTIONS:
                return None
            params = row.get("params") or {}
            if not isinstance(params, dict):
                return None
            out.append({"action": action, "params": params, "name": name})
        return {"steps": out}

    @classmethod
    def load(cls, path: Path | str | None = None) -> "MidiMapper":
        """Built-in map, overridden by a JSON file when one exists.

        Raises ValueError/OSError for a broken file - callers (the
        manager) degrade to the default map and report the error.
        """
        if path:
            p = Path(path)
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"bad MIDI map {p.name}: {exc}") from exc
                return cls(data)
        return cls(DEFAULT_MAP)

    # -- resolve ----------------------------------------------------------
    def resolve(self, event: dict) -> list[tuple[str, dict]]:
        """Event -> engine steps (empty list = unmapped)."""
        table = self.notes if event.get("kind") == "note" else self.ccs
        number = event.get("number")
        key = str(number)
        entry = table.get(key)
        if entry is None and event.get("kind") == "note":
            entry = table.get(f"{number}:{event.get('channel')}")  # per-ch
        elif entry is None:
            entry = table.get(f"{number}:{event.get('channel')}")
        if entry is None:
            return []
        # Notes fire on press; a mapping that uses $on opts in to the
        # release event too (momentary switches like blackout).
        if event.get("kind") == "note" and not event.get("on"):
            raw = json.dumps(entry)
            if "$on" not in raw:
                return []
        steps = []
        for row in entry["steps"]:
            params = {k: _substitute(v, event) for k, v in
                      row["params"].items()}
            steps.append((row["action"], params))
        return steps


# -- sources --------------------------------------------------------------
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


def enumerate_devices() -> list[dict]:
    """[{"index": i, "name": str}, ...] via winmm; [] off-Windows."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes
        winmm = ctypes.WinDLL("winmm")

        class MIDIINCAPS(ctypes.Structure):
            _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                        ("vDriverVersion", wintypes.DWORD),
                        ("szPname", wintypes.WCHAR * 32),
                        ("dwSupport", wintypes.DWORD)]

        count = int(winmm.midiInGetNumDevs())
        devices = []
        for index in range(count):
            caps = MIDIINCAPS()
            if winmm.midiInGetDevCapsW(index, ctypes.byref(caps),
                                       ctypes.sizeof(caps)) == 0:
                devices.append({"index": index, "name": caps.szPname})
        return devices
    except Exception:                          # noqa: BLE001
        return []


def _winmm_open(index: int):
    """Open one winmm input device; returns (source, close_fn)."""
    import ctypes
    from ctypes import wintypes
    winmm = ctypes.WinDLL("winmm")
    handle = ctypes.c_void_p(0)
    CallbackType = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, wintypes.UINT,
                                      ctypes.c_size_t, wintypes.DWORD,
                                      wintypes.DWORD)
    source = _WinSource()

    def _callback(hmidi, msg, instance, param1, param2):  # noqa: ANN001
        if msg == MIM_DATA:
            source.queue.put(("msg", param1 & 0xFFFFFFFF,
                              (param1 >> 8) & 0xFF,
                              (param1 >> 16) & 0xFF))
        elif msg == MIM_CLOSE:
            source.queue.put(("close",))

    source.callback = CallbackType(_callback)   # keep a reference: GC kills it
    result = winmm.midiInOpen(ctypes.byref(handle), index,
                              ctypes.cast(source.callback, ctypes.c_void_p),
                              0, CALLBACK_FUNCTION)
    if result != MMSYSERR_NOERROR:
        buf = ctypes.create_unicode_buffer(256)
        try:
            winmm.midiInGetErrorTextW(result, buf, 256)
            detail = buf.value
        except Exception:                       # noqa: BLE001
            detail = f"code {result}"
        raise OSError(f"cannot open MIDI input {index}: {detail}")
    winmm.midiInStart(handle)

    def _close() -> None:
        try:
            winmm.midiInStop(handle)
            winmm.midiInClose(handle)
        except Exception:                       # noqa: BLE001
            pass

    source.kind = "winmm"
    source.close = _close
    return source, _close


class _WinSource:
    """Minimal source object filled in by _winmm_open()."""

    kind = "winmm"

    def __init__(self):
        self.queue: queue.Queue = queue.Queue()
        self.callback = None
        self.closed = False

    def close(self) -> None:
        self.closed = True


# -- manager --------------------------------------------------------------
class MidiManager:
    """Owns the source, the reader thread and the mapping pipeline."""

    def __init__(self, engine, mapper: MidiMapper | None = None,
                 device: str | int | None = None, map_path: str | None = None,
                 source=None, devices: list[dict] | None = None):
        self.engine = engine
        self.map_path = map_path
        self.mapper = mapper if mapper is not None else \
            MidiMapper.load(map_path) if map_path else MidiMapper()
        self.device = device
        self.source = source                  # injected (tests) or winmm
        # Injected device list (tests): None = enumerate the real system,
        # [] = deterministically "no devices present" (no hardware touch).
        self.devices = devices
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.map_error: str | None = None
        self.status = {"enabled": True, "open": False, "devices": [],
                       "device": None, "error": None, "events": 0,
                       "mapped": 0, "map_skipped": list(self.mapper.skipped)}
        # the last messages and what each did, for the MIDI monitor
        self.recent: collections.deque = collections.deque(maxlen=40)

    # -- devices ----------------------------------------------------------
    @staticmethod
    def enumerate() -> list[dict]:
        return enumerate_devices()

    def _pick(self, devices: list[dict]):
        """Device selector: exact/substring name, numeric index, or first."""
        want = self.device
        if want in (None, ""):
            return devices[0] if devices else None
        text = str(want).strip()
        for row in devices:
            if row["name"] == text:
                return row
        for row in devices:
            if text.lower() in row["name"].lower():
                return row
        try:
            index = int(text)
        except ValueError:
            return None
        for row in devices:
            if row["index"] == index:
                return row
        return None

    def open(self) -> bool:
        """Open the selected input device (idempotent, never raises)."""
        if self.source is not None and self.status["open"]:
            return True
        devices = self.devices if self.devices is not None else \
            self.enumerate()
        self.status["devices"] = devices
        if self.source is None:
            pick = self._pick(devices)
            if pick is None:
                self.status.update(open=False, device=None,
                                   error=("no MIDI input devices found"
                                          if not devices else
                                          f"no device matches "
                                          f"{self.device!r}"))
                return False
            try:
                self.source, _ = _winmm_open(pick["index"])
            except OSError as exc:
                self.status.update(open=False, device=None, error=str(exc))
                return False
            self.status["device"] = f"{pick['index']}: {pick['name']}"
        self.status.update(open=True, error=None)
        return True

    def start(self) -> bool:
        """Open the device and start the reader thread (graceful if none)."""
        if self._thread is not None and self._thread.is_alive():
            return True
        if not self.open():
            return False                        # no device: idle, no crash
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop,
                                        name="jarvis-midi", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() \
                and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        if self.source is not None:
            try:
                self.source.close()
            except Exception:                   # noqa: BLE001
                pass
        self.status["open"] = False

    def reload_map(self, path: str | None = None) -> dict:
        """Swap mappings at runtime (operator edits data/midi_map.json)."""
        try:
            self.mapper = MidiMapper.load(path or self.map_path)
            self.map_error = None
        except (OSError, ValueError) as exc:
            self.map_error = str(exc)
            return {"ok": False, "error": self.map_error}
        self.status["map_skipped"] = list(self.mapper.skipped)
        return {"ok": True, "mapped": len(self.mapper.notes)
                + len(self.mapper.ccs),
                "skipped": list(self.mapper.skipped)}

    # -- event pipeline ---------------------------------------------------
    def handle(self, event: dict) -> list[dict]:
        """Resolve + execute one event against the engine (thread-safe:
        engine.act takes the engine lock itself)."""
        results = self._handle(event)
        did = ", ".join(r.get("summary") or r.get("action") or "" for r in results if r.get("ok"))
        bad = next((r.get("error") for r in results if not r.get("ok")), None)
        self.recent.append({"kind": event.get("kind"), "channel": event.get("channel"),
                            "number": event.get("number"), "value": event.get("value"),
                            "on": event.get("on"), "at": time.time(),
                            "did": did or (f"failed: {bad}" if bad else "")})
        return results

    def _handle(self, event: dict) -> list[dict]:
        results = []
        if event.get("kind") == "note":
            # remembered for "Learn" in the button editor
            self.status["last_note"] = {"number": event["number"], "channel": event.get("channel"),
                                        "at": time.time()}
            # a note given to a button plays it, ahead of the map file;
            # letting go only matters to a hold button
            buttons = [b for b in list(getattr(self.engine, "quick", []) or [])
                       if b.get("midi") == event["number"]]
            if buttons:
                for b in buttons:
                    if not event.get("on") and b.get("mode") != "hold":
                        continue
                    try:
                        results.append(self.engine.act("quick_press", id=b["id"], down=bool(event.get("on"))))
                    except Exception as exc:    # noqa: BLE001 - never die
                        results.append({"ok": False, "action": "quick_press", "error": str(exc)})
                self.status["events"] += 1
                self.status["mapped"] += 1
                return results
        steps = self.mapper.resolve(event)
        for action, params in steps:
            try:
                results.append(self.engine.act(action, **params))
            except Exception as exc:            # noqa: BLE001 - never die
                results.append({"ok": False, "action": action,
                                "error": str(exc)})
        self.status["events"] += 1
        if steps:
            self.status["mapped"] += 1
        return results

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self.source.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            except Exception:                   # noqa: BLE001
                break                            # source gone
            if not item or item[0] == "close":
                if item and item[0] == "close":
                    self.status["open"] = False
                    self.status["error"] = "MIDI device disconnected"
                if self._stop.is_set():
                    break
                continue
            try:
                event = parse_short(item[1], item[2], item[3])
            except Exception:                   # noqa: BLE001
                continue
            if event is not None:
                self.handle(event)


# -- module singleton (wired by main.py) ---------------------------------
MANAGER: MidiManager | None = None


def start_from_config(engine, config) -> dict:
    """Create + start the MIDI manager from config (boot path)."""
    global MANAGER
    stop()
    if not config.MIDI_ENABLED:
        return {"enabled": False}
    try:
        manager = MidiManager(engine, device=config.MIDI_DEVICE or None,
                              map_path=(config.MIDI_MAP or
                                        str(config.DATA / "midi_map.json")))
    except ValueError as exc:                   # broken map file
        manager = MidiManager(engine, device=config.MIDI_DEVICE or None)
        manager.map_error = str(exc)
    MANAGER = manager
    manager.start()
    return status()


def stop() -> None:
    global MANAGER
    if MANAGER is not None:
        MANAGER.stop()
        MANAGER = None


def status() -> dict:
    if MANAGER is None:
        return {"enabled": False, "open": False, "devices": [],
                "device": None, "error": None, "events": 0, "mapped": 0}
    data = dict(MANAGER.status)
    data["enabled"] = True
    data["recent"] = list(MANAGER.recent)
    if MANAGER.map_error:
        data["map_error"] = MANAGER.map_error
    return data
