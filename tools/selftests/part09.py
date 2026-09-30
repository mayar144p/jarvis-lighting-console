"""Self-test suites, part 9: the virtual node and the MIDI monitor."""
from __future__ import annotations

import socket
import tempfile
import time
from pathlib import Path

from app import config, fixtures
from tools.selftests.common import ROOT, check


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_virtual_node() -> None:
    """The desk's own virtual Art-Net node: the output goes to it when it
    is on, it keeps exactly what the desk sent, every patched light
    answers RDM like a real one (and can be readdressed without touching
    the patch), and turning it off puts the output back."""
    print("virtual node")
    from app import engine as eng
    from app import rdm

    saved_port = config.DMX_PORT
    config.DMX_PORT = _free_port()
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "f.db"
            fixtures.seed_generics(db)
            e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
            try:
                e.act("add_heads", query="LED PAR 4ch", qty=3, universe=1, address=1)
                e.act("add_heads", query="LED PAR 4ch", qty=1, universe=2, address=10)
                before = dict(e.dmx_target)
                r = e.act("virtual_node", state=True)
                check("the virtual node turns on and takes the output",
                      r.get("ok") and e.dmx_target["host"] == "127.0.0.1"
                      and e._output_public()["virtual_node"], str(r))
                check("...without an undo step", not e._undo or e._undo[-1].get("action") != "virtual_node", "")
                e.act("select_all")
                e.act("set_intensity", level=100)
                e.act("set_colour", hex="#ff0000")
                e.act("set_dry_run", state=False, confirm=True)
                e.act("set_output", state=1, confirm=True)
                deadline = time.monotonic() + 3
                st = {}
                while time.monotonic() < deadline:
                    st = e.vnode_status(1)
                    if st.get("frame") and any(st["frame"]) and len(st.get("universes") or []) == 2 \
                            and st["universes"][0]["fps"] > 0:
                        break
                    time.sleep(0.05)
                with e.lock:
                    want = e.build_frames()
                check("it receives every universe the desk sends", [u["universe"] for u in st.get("universes") or []] == [1, 2],
                      str(st.get("universes")))
                check("...byte for byte", st.get("frame") == list(want[1]), str((st.get("frame") or [])[:12]))
                check("...at the output rate", (st["universes"][0]["fps"] if st.get("universes") else 0) >= 20,
                      str(st.get("universes")))
                e.act("set_output", state=0)
                found = rdm.discover([1, 2], host="127.0.0.1", port=config.DMX_PORT, timeout=0.5, per_request=0.2)
                check("every patched light answers RDM", len(found["devices"]) == 4, str(found)[:200])
                r = e.act("rdm_compare", devices=found["devices"], universes=[1, 2])
                check("...and matches the patch", r.get("ok") and all(d["status"] == "ok" for d in r["devices"])
                      and not r["silent"], r.get("summary"))
                uid = next(d["uid"] for d in found["devices"] if d["universe"] == 2)
                r = rdm.set_address(uid, 2, 100, host="127.0.0.1", port=config.DMX_PORT)
                light = next(x for x in e.vnode_status()["lights"] if x["uid"] == uid)
                check("a light can be readdressed; the patch stays as it was",
                      r.get("ok") and light["address"] == 100 and e.patch[3]["address"] == 10, str((r, light)))
                r = e.act("virtual_node", state=False)
                check("off puts the output back where it was", r.get("ok") and e.dmx_target == before
                      and not e._output_public()["virtual_node"], str(e.dmx_target))
            finally:
                e.shutdown()
    finally:
        config.DMX_PORT = saved_port
    mj = (ROOT / "web" / "app" / "monitors.js").read_text(encoding="utf-8")
    dj = (ROOT / "web" / "app" / "dialogs.js").read_text(encoding="utf-8")
    check("Settings opens the node monitor, which draws the 512 channels",
          "openNodeMonitor" in dj and "/api/console/vnode" in mj and "512" in mj, "")


def test_midi_monitor() -> None:
    """Every MIDI message is remembered with what it did, for the monitor;
    the browser's MIDI shows every message too, not only notes."""
    print("MIDI monitor")
    from app import engine as eng
    from app import midi

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            m = midi.MidiManager(e, devices=[])
            e.act("quick_set", page=1, slot=1, button={"kind": "flash", "label": "Hit", "midi": 36})
            m.handle({"kind": "note", "channel": 1, "number": 36, "value": 100, "on": True})
            m.handle({"kind": "cc", "channel": 2, "number": 99, "value": 64})
            rec = list(m.recent)
            check("each message is kept with what it did", len(rec) == 2 and rec[0]["number"] == 36
                  and rec[0]["did"] and rec[1]["kind"] == "cc" and rec[1]["did"] == "", str(rec))
            midi.MANAGER = m
            try:
                check("the status carries them", [r["number"] for r in midi.status()["recent"]] == [36, 99], "")
            finally:
                midi.MANAGER = None
        finally:
            e.shutdown()
    wj = (ROOT / "web" / "app" / "webmidi.js").read_text(encoding="utf-8")
    check("browser MIDI hands every message to the monitor", "onAnyMidi" in wj, "")
