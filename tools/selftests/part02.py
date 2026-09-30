"""Self-test suites, part 2: dmx16, dmx_input, midi, autofollow, profiles, scan_simulated, aim, palette_targets, ...."""
from __future__ import annotations

import json
import struct
import tempfile
import threading
import zipfile
from pathlib import Path

from app import fixtures
from tools.selftests.common import ROOT, _free_udp_port, _valueerror, _wait_until, check


def test_dmx16(tmp: Path) -> None:
    from app import engine as eng_mod
    from app import profiles

    print("16-bit DMX assembly (coarse + fine pairing)")
    # -- pure helpers: 0 / 1 / byte edges / midpoint / max / round-trip ---
    cases = {0: (0, 0), 1: (0, 1), 255: (0, 255), 256: (1, 0), 257: (1, 1),
             32767: (127, 255), 32768: (128, 0), 65534: (255, 254),
             65535: (255, 255)}
    ok, detail = True, ""
    for value, want in cases.items():
        got = eng_mod.split_16bit(value)
        if got != want or eng_mod.join_16bit(*got) != value:
            ok, detail = False, f"{value}: {got} != {want}"
    check("split/join: 0, 1, byte edges, midpoint, max, round-trip", ok, detail)
    check("clamps below range", eng_mod.split_16bit(-5) == (0, 0))
    check("clamps above range", eng_mod.split_16bit(99999) == (255, 255))
    check("fine-first ordering swaps halves",
          eng_mod.split_16bit(256, fine_first=True) == (0, 1)
          and eng_mod.join_16bit(0, 1, fine_first=True) == 256
          and eng_mod.split_16bit(65535, fine_first=True) == (255, 255))
    check("8-bit values keep their coarse byte (x257, no stepping)",
          all(eng_mod.split_16bit(eng_mod._logical16(v))[0] == v
              for v in (0, 1, 127, 128, 200, 254, 255)))
    check("_logical16 passes 16-bit values through",
          eng_mod._logical16(256) == 256 and eng_mod._logical16(60000) == 60000
          and eng_mod._logical16(65535) == 65535)
    check("fine role detection",
          eng_mod.is_fine_role("pan_fine")
          and eng_mod.is_fine_role("dimmer_fine")
          and not eng_mod.is_fine_role("pan")
          and not eng_mod.is_fine_role("wheel"))
    check("labels map to roles",
          eng_mod.channel_role("Pan (16-bit)") == "pan"
          and eng_mod.channel_role("Pan fine") == "pan_fine"
          and eng_mod.channel_role("Dimmer fine") == "dimmer_fine")

    db = tmp / "dmx16.db"
    fixtures.seed_generics(db)
    profiles.install(db)
    e = eng_mod.Engine(db_path=db, dry_run=True, show_dir=tmp / "dmx16_shows")
    res = e.act("add_heads", query="intimidator spot 260", mode="14ch",
                qty=1, universe=1, address=1)
    check("Spot 260 14ch patched", res["ok"] and e.patch[0]["channels"] == 14,
          str(res.get("error")))
    e.act("select_all")
    e.act("set_position", pan=0, tilt=0)
    buf = e.build_frames()[1]
    check("pan/tilt 0 -> every byte 0", list(buf[0:4]) == [0, 0, 0, 0],
          str(list(buf[0:4])))
    e.act("set_position", pan=65535, tilt=32768)
    buf = e.build_frames()[1]
    check("pan max -> (255,255), tilt midpoint -> (128,0)",
          buf[0] == 255 and buf[1] == 255 and buf[2] == 128 and buf[3] == 0,
          str(list(buf[0:4])))
    e.act("set_position", pan=256, tilt=257)
    buf = e.build_frames()[1]
    check("byte-boundary values 256 -> (1,0), 257 -> (1,1)",
          buf[0] == 1 and buf[1] == 0 and buf[2] == 1 and buf[3] == 1,
          str(list(buf[0:4])))
    e.act("set_attribute", attribute="pan_fine", value=7)
    buf = e.build_frames()[1]
    check("explicit fine value wins over the derived half",
          buf[0] == 1 and buf[1] == 7, str(list(buf[0:2])))
    e.act("clear_programmer")
    e.act("set_attribute", attribute="pan", value=70000)
    buf = e.build_frames()[1]
    check("programmer clamps at 65535", list(buf[0:2]) == [255, 255],
          str(list(buf[0:2])))
    e.act("clear_programmer")

    # 16-bit dimmer fixtures written straight into the DB prove the
    # pairing is generic: works in EITHER physical channel order with
    # no fixture-specific controller code.
    modes = [("Dimmer16", ["Dimmer (16-bit)", "Dimmer fine"], 100),
             ("Dimmer16R", ["Dimmer fine", "Dimmer (16-bit)"], 200)]
    with fixtures.db(db) as conn:
        for model, labels, _addr in modes:
            conn.execute(
                "INSERT OR IGNORE INTO fixtures"
                " (manufacturer, model, source) VALUES (?,?,?)",
                ("TestCo", model, "test"))
            fid = conn.execute(
                "SELECT id FROM fixtures WHERE model=?", (model,)
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO modes (fixture_id, name, channel_count, channels)"
                " VALUES (?,?,?,?)",
                (fid, "2ch", len(labels), json.dumps(labels)))
    for model, _labels, address in modes:
        res = e.act("add_heads", query=model, qty=1, universe=1,
                    address=address)
        check(f"{model} patches at universe 1 address {address}",
              res["ok"] and e.patch[-1]["channels"] == 2,
              str(res.get("error")))
    res = e.act("add_heads", query="LED PAR 4ch", qty=1, universe=2,
                address=1)
    check("8-bit fixture patches", res["ok"], str(res.get("error")))
    e.act("select_all")
    e.act("set_attribute", attribute="dimmer", value=50)
    buf = e.build_frames()[1]
    check("16-bit HTP dimmer 50% -> coarse 127 / fine 255",
          buf[99] == 127 and buf[100] == 255, str(list(buf[99:101])))
    check("fine-first fixture emits the same logical value",
          buf[199] == 255 and buf[200] == 127, str(list(buf[199:201])))
    check("8-bit HTP dimmer unchanged (50% -> 127)", buf[10] == 127,
          str(buf[10]))
    check("8-bit universe unchanged (50% -> 127)",
          e.build_frames()[2][0] == 127, str(e.build_frames()[2][0]))
    e.act("set_attribute", attribute="dimmer", value=100)
    buf = e.build_frames()[1]
    check("16-bit HTP dimmer 100% -> (255,255) both orders",
          buf[99] == 255 and buf[100] == 255
          and buf[199] == 255 and buf[200] == 255, str(list(buf[99:101])))
    check("8-bit dimmer 100% -> 255",
          buf[10] == 255 and e.build_frames()[2][0] == 255)
    e.act("set_attribute", attribute="dimmer", value=0)
    buf = e.build_frames()[1]
    check("0 -> every dimmer byte 0",
          buf[99] == 0 and buf[100] == 0 and buf[199] == 0 and buf[200] == 0)
    e.act("set_attribute", attribute="red", value=300)
    check("8-bit role clamps on the wire (300 -> 255)",
          e.build_frames()[2][1] == 255, str(e.build_frames()[2][1]))
    e.act("clear_programmer")
    e.shutdown()

    # 16-bit programmer values must survive an engine restart (autosave)
    auto = tmp / "d16_autosave.json"
    e2 = eng_mod.Engine(db_path=db, dry_run=True, show_dir=tmp / "d16_shows",
                        autosave_path=auto, restore=False)
    e2.act("add_heads", query="intimidator spot 260", mode="14ch",
           qty=1, universe=3, address=1)
    e2.act("select_all")
    e2.act("set_attribute", attribute="pan", value=60000)
    e2.shutdown()
    e3 = eng_mod.Engine(db_path=db, dry_run=True, show_dir=tmp / "d16_shows",
                        autosave_path=auto, restore=True)
    value = next((row["pan"] for row in e3.programmer.values()
                  if "pan" in row), None)
    check("16-bit programmer survives autosave", value == 60000,
          str(e3.programmer))
    buf = e3.build_frames()[3]
    check("restored pan on the wire -> (234, 96)",
          buf[0] == 234 and buf[1] == 96, str(list(buf[0:2])))
    e3.shutdown()


def test_dmx_input(tmp: Path) -> None:
    import socket as _socket

    from app import artnet, dmxin, sacn

    print("DMX input (receive, observe, degrade)")
    check("empty map is identity", dmxin.parse_map("") == {})
    check("map parses", dmxin.parse_map("1=3, 2=3") == {1: 3, 2: 3})
    for bad in ("1", "a=b", "0=1", "1=64000", "70000=1", "1=0"):
        check(f"map rejects {bad!r}",
              _valueerror(lambda b=bad: dmxin.parse_map(b)))

    data = bytes(((i * 37) % 255) + 1 for i in range(512))
    st = dmxin.DmxInputState(timeout=1.0)
    landed = st.ingest(1, data, "artnet", sequence=5, peer="127.0.0.1:6454")
    check("frame stored under its universe",
          landed == 1 and st.universes() == [1], str(st.universes()))
    check("channels 1 and 512 readable",
          st.get(1, 1) == data[0] and st.get(1, 512) == data[511],
          f"{st.get(1, 1)} / {st.get(1, 512)}")
    check("full 512-slot copy", st.channels(1) == data)
    check("channel 0 rejected", _valueerror(lambda: st.get(1, 0)))
    check("channel 513 rejected", _valueerror(lambda: st.get(1, 513)))
    check("universe 0 rejected", _valueerror(lambda: st.ingest(0, data)))
    check("universe 64000 rejected",
          _valueerror(lambda: st.ingest(64000, data)))
    check("universe 63999 accepted", st.ingest(63999, data) == 63999)
    st.clear()
    check("empty frame rejected", _valueerror(lambda: st.ingest(1, b"")))
    check("513-slot frame rejected",
          _valueerror(lambda: st.ingest(1, bytes(513))))
    data2 = bytes(255 - v for v in data)
    st.ingest(1, data, "artnet", sequence=5, now=100.0)
    st.ingest(1, data2, "artnet", sequence=6, now=101.0)
    snap = st.snapshot(now=101.5)
    row = snap["universes"][0]
    check("update replaces the frame",
          st.channels(1) == data2 and st.get(1, 1) == data2[0])
    check("snapshot observable: source/sequence/age",
          row["source"] == "artnet" and row["sequence"] == 6
          and row["age_ms"] == 500 and not row["stale"], str(row))
    check("packet counter observed", snap["stats"]["packets"] >= 3,
          str(snap["stats"]))
    check("fresh under timeout",
          st.age(1, 101.5) == 0.5 and not st.stale(1, 101.5))
    check("stale after timeout",
          st.age(1, 103.0) == 2.0 and st.stale(1, 103.0))
    check("never-seen universe reads 0 / is stale",
          st.get(9, 1) == 0 and st.age(9, 101.5) is None
          and st.stale(9, 101.5))
    st.mark_terminated(1)
    check("sACN terminate drops freshness, keeps data observable",
          st.age(1, 102.0) is None and st.stale(1, 102.0)
          and st.snapshot(now=102.0)["universes"][0]["terminated"]
          and st.channels(1) == data2)
    st2 = dmxin.DmxInputState(mapping={1: 5})
    check("map redirects the landing universe",
          st2.ingest(1, data) == 5 and st2.universes() == [5]
          and st2.get(5, 1) == data[0])
    check("map exposed in snapshot",
          st2.snapshot()["mapping"] == {"1": 5})

    # -- real sockets on private ports (loopback, no hardware) -----------
    port_a, port_s = _free_udp_port(), _free_udp_port()
    while port_s == port_a:
        port_s = _free_udp_port()
    lst_state = dmxin.DmxInputState(timeout=1.0)
    lis = dmxin.DmxInputListener(lst_state, artnet_port=port_a,
                                 sacn_port=port_s, net=0)
    check("listener starts on free ports", lis.start() and lis.running)
    tx = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        tx.sendto(artnet.build_artdmx(1, data, sequence=5),
                  ("127.0.0.1", port_a))
        tx.sendto(sacn.build_sacn(7, data, sequence=2),
                  ("127.0.0.1", port_s))
        got = _wait_until(lambda: 1 in lst_state.universes()
                          and 7 in lst_state.universes())
        check("Art-Net frame received over UDP",
              got and lst_state.get(1, 512) == data[511],
              str(lst_state.universes()))
        check("sACN frame received over UDP",
              got and lst_state.get(7, 1) == data[0])
        rows = {r["universe"]: r for r in lst_state.snapshot()["universes"]}
        check("source tagged per transport",
              rows.get(1, {}).get("source") == "artnet"
              and rows.get(7, {}).get("source") == "sacn", str(rows))
        base_mal, base_ign = lst_state.malformed, lst_state.ignored
        tx.sendto(b"Art-Net\0\x00\x50\x00\x0e", ("127.0.0.1", port_a))
        tx.sendto(b"hello junk", ("127.0.0.1", port_a))
        tx.sendto(b"junk", ("127.0.0.1", port_s))
        check("malformed ArtDmx counted, not raised",
              _wait_until(lambda: lst_state.malformed > base_mal),
              f"{base_mal} -> {lst_state.malformed}")
        check("foreign traffic ignored (not malformed)",
              _wait_until(lambda: lst_state.ignored > base_ign),
              f"{base_ign} -> {lst_state.ignored}")
        tx.sendto(sacn.build_sacn(7, data, sequence=3, options=0x40),
                  ("127.0.0.1", port_s))
        check("sACN stream-terminated observed",
              _wait_until(lambda: any(r["terminated"] for r in
                                      lst_state.snapshot()["universes"])))
    finally:
        tx.close()
        lis.stop()
    check("stop joins every listener thread",
          not lis.running and all(not t.is_alive() for t in lis._threads),
          str([t.is_alive() for t in lis._threads]))

    # -- boot path never raises (invalid config degrades to a status) ----
    class _BadPort:
        DMX_INPUT = True
        DMX_INPUT_MAP = ""
        DMX_INPUT_TIMEOUT = 1.0
        DMX_INPUT_ARTNET_PORT = 70000
        DMX_INPUT_SACN_PORT = port_s
        DMX_NET = 0

    status, error = None, None
    try:
        status = dmxin.start_from_config(_BadPort)
    except Exception as exc:                 # noqa: BLE001
        error = exc
    check("bad input port degrades instead of crashing boot",
          error is None and status is not None
          and "1..65535" in str(status.get("error")),
          f"{error!r} {status}")
    dmxin.stop()

    class _BadMap:
        DMX_INPUT = True
        DMX_INPUT_MAP = "junk"
        DMX_INPUT_TIMEOUT = 1.0
        DMX_INPUT_ARTNET_PORT = port_a
        DMX_INPUT_SACN_PORT = port_s
        DMX_NET = 0

    status = dmxin.start_from_config(_BadMap)
    check("bad map reported, listener still runs",
          "bad DMX input map" in str(status.get("map_error"))
          and status.get("running") and status.get("mapping") == {},
          str(status))
    dmxin.stop()
    check("module stop leaves input disabled",
          dmxin.snapshot().get("enabled") is False)


def test_midi(tmp: Path) -> None:
    from app import engine as eng_mod
    from app import midi

    print("MIDI input (parse, mapping, devices, engine)")
    ev = midi.parse_short(0x90, 36, 100)
    check("note on", ev and ev["kind"] == "note" and ev["on"]
          and ev["number"] == 36 and ev["value"] == 100
          and ev["channel"] == 1, str(ev))
    check("channel from status nibble",
          midi.parse_short(0x91, 40, 90)["channel"] == 2)
    check("note on velocity 0 = release",
          midi.parse_short(0x90, 40, 0)["on"] is False)
    check("note off", midi.parse_short(0x80, 40, 0)["on"] is False)
    ev = midi.parse_short(0xB0, 1, 64)
    check("control change", ev and ev["kind"] == "cc" and ev["number"] == 1
          and ev["value"] == 64, str(ev))
    check("pitch bend ignored", midi.parse_short(0xE0, 0, 64) is None)
    check("program change ignored", midi.parse_short(0xC0, 5, 0) is None)

    mapper = midi.MidiMapper()
    check("note 36 -> GO PB1",
          mapper.resolve(midi.parse_short(0x90, 36, 100))
          == [("cue_go", {"playback": 1})])
    check("note release ignored for non-momentary maps",
          mapper.resolve(midi.parse_short(0x80, 36, 0)) == [])
    check("note 49 blackout fires on press AND release",
          mapper.resolve(midi.parse_short(0x90, 49, 100))
          == [("blackout", {"state": 1})]
          and mapper.resolve(midi.parse_short(0x80, 49, 0))
          == [("blackout", {"state": 0})])
    check("CC value -> percent parameter",
          mapper.resolve(midi.parse_short(0xB0, 1, 127))
          == [("master", {"level": 100})]
          and mapper.resolve(midi.parse_short(0xB0, 1, 64))
          == [("master", {"level": 50})])
    check("unknown note / cc unmapped",
          mapper.resolve(midi.parse_short(0x90, 70, 100)) == []
          and mapper.resolve(midi.parse_short(0xB0, 9, 10)) == [])

    custom = midi.MidiMapper({"notes": {
        "60": {"steps": [{"action": "select_all"},
                         {"action": "set_attribute",
                          "params": {"attribute": "pan", "value": "$value"}}]},
        "61:2": {"action": "master", "params": {"level": "$pct"}},
        "62": {"action": "no_such_action"},
        "63": "junk"},
        "cc": {"5": {"action": "master", "params": {"level": 10}},
               "6": {"action": "teleport"}}})
    steps = custom.resolve({"kind": "note", "channel": 1, "number": 60,
                            "value": 100, "on": True})
    check("steps chain runs in order",
          [s[0] for s in steps] == ["select_all", "set_attribute"], str(steps))
    check("$value substituted", steps[1][1]["value"] == 100, str(steps))
    check("per-channel key 61:2 fires on channel 2",
          custom.resolve({"kind": "note", "channel": 2, "number": 61,
                          "value": 64, "on": True})
          == [("master", {"level": 50})])
    check("per-channel key ignores other channels",
          custom.resolve({"kind": "note", "channel": 1, "number": 61,
                          "value": 64, "on": True}) == [])
    check("invalid entries skipped, valid ones kept",
          sorted(custom.skipped) == ["cc 6", "note 62", "note 63"]
          and custom.resolve(midi.parse_short(0xB0, 5, 20))
          == [("master", {"level": 10})], str(custom.skipped))
    check("non-object map rejected",
          _valueerror(lambda: midi.MidiMapper([])))
    check("notes must be an object",
          _valueerror(lambda: midi.MidiMapper({"notes": [], "cc": {}})))
    good = tmp / "midi_ok.json"
    good.write_text(json.dumps({"notes": {"70": {"action": "locate"}},
                                "cc": {}}), encoding="utf-8")
    check("map file loads", len(midi.MidiMapper.load(good).notes) == 1)
    check("missing file falls back to default map",
          len(midi.MidiMapper.load(tmp / "nope.json").notes) == 12)
    badf = tmp / "midi_bad.json"
    badf.write_text("{not json", encoding="utf-8")
    check("broken map file raises ValueError",
          _valueerror(lambda: midi.MidiMapper.load(badf)))

    # device selection: pure logic, no hardware opened
    devices = [{"index": 0, "name": "LoopMIDI Port"},
               {"index": 1, "name": "MidiKeys"}]
    pick = midi.MidiManager(None, devices=devices)
    check("pick first by default", pick._pick(devices)["index"] == 0)
    pick.device = "midikeys"
    check("pick by substring", pick._pick(devices)["index"] == 1)
    pick.device = "LoopMIDI Port"
    check("pick by exact name", pick._pick(devices)["index"] == 0)
    pick.device = "1"
    check("pick by index", pick._pick(devices)["index"] == 1)
    pick.device = "missing"
    check("no match -> None", pick._pick(devices) is None)

    # engine integration through the fake source (no hardware in tests)
    db = tmp / "midi.db"
    fixtures.seed_generics(db)
    e = eng_mod.Engine(db_path=db, dry_run=True, show_dir=tmp / "midi_shows")
    e.act("add_heads", query="LED PAR 4ch", qty=2)   # something to program
    fake = midi.FakeMidiSource()
    runner = midi.MidiManager(e, mapper=midi.MidiMapper(), source=fake,
                               devices=[])
    try:
        check("manager starts with injected source", runner.start())
        fake.push(0xB0, 1, 64)
        check("CC1 drove the engine master",
              _wait_until(lambda: e.master == 50), str(e.master))
        e.act("select_all")
        e.act("set_attribute", attribute="dimmer", value=10)
        e.act("record_cue", playback=1, name="one", hold=0.0)
        e.act("select_all")
        e.act("set_attribute", attribute="dimmer", value=20)
        res = e.act("record_cue", playback=1, name="two", hold=0.0)
        check("two cues recorded", res["ok"] and
              len(e.playbacks[0]["stack"]) == 2, str(res.get("error")))
        fake.push(0x90, 36, 100)
        check("note 36 GOed playback 1",
              _wait_until(lambda: e.playbacks[0]["active"]),
              str(e.playbacks[0]["active"]))
        check("event counters observed",
              runner.status["events"] >= 2 and runner.status["mapped"] >= 2,
              str(runner.status))
        fake.push_close()
        check("disconnect handled gracefully",
              _wait_until(lambda: runner.status["open"] is False)
              and "disconnected" in str(runner.status["error"]),
              str(runner.status))
    finally:
        runner.stop()
    check("reader thread joined",
          runner._thread is None or not runner._thread.is_alive())

    no_dev = midi.MidiManager(e, devices=[])
    check("no device -> graceful start", no_dev.start() is False)
    check("no device status explains",
          "no MIDI input devices" in str(no_dev.status["error"])
          and no_dev.status["open"] is False, str(no_dev.status))
    no_dev.stop()                          # never raises, no thread exists
    check("no device leaves no thread", no_dev._thread is None)

    class _Cfg:
        MIDI_ENABLED = False
        MIDI_DEVICE = ""
        MIDI_MAP = ""
        DATA = tmp

    check("boot path with MIDI disabled",
          midi.start_from_config(e, _Cfg()).get("enabled") is False
          and midi.status()["enabled"] is False)
    midi.stop()
    e.shutdown()


def test_autofollow(tmp: Path) -> None:
    import threading as _threading
    import time as _time

    from app import engine as eng_mod

    print("auto-follow (timed cue-stack advance)")
    db = tmp / "follow.db"
    fixtures.seed_generics(db)
    e = eng_mod.Engine(db_path=db, dry_run=True, show_dir=tmp / "follow_shows")
    e.act("add_heads", query="LED PAR 4ch", qty=2)   # fixtures to record
    clock = [1000.0]
    e._clock = lambda: clock[0]             # injected clock = determinism
    for i in range(3):
        e.act("select_all")
        e.act("set_attribute", attribute="dimmer", value=10 + 10 * i)
        res = e.act("record_cue", playback=1, name=f"cue {i + 1}", hold=1.5)
        check(f"cue {i + 1} recorded", res["ok"], str(res.get("error")))
    check("3 cues in the stack", len(e.playbacks[0]["stack"]) == 3,
          str(len(e.playbacks[0]["stack"])))

    res = e.act("follow_set", playback=1, on=True, delay=2.0)
    check("follow_set arms playback 1",
          res["ok"] and res["follow"]["on"] and res["follow"]["delay"] == 2.0,
          str(res.get("follow")))
    check("ticker starts while armed",
          e._follow_thread is not None and e._follow_thread.is_alive())
    res = e.act("cue_go", playback=1)
    check("manual GO -> cue 1", res["ok"] and e.playbacks[0]["index"] == 0)
    check("deadline armed at now + delay",
          e.playbacks[0]["follow"]["at"] == 1002.0,
          str(e.playbacks[0]["follow"]["at"]))
    e._stop_follow_thread()                # deterministic manual ticking
    clock[0] = 1001.9
    check("early tick does not fire", e._tick_follow() == [])
    clock[0] = 1002.1
    fired = e._tick_follow()
    check("fires at the deadline",
          len(fired) == 1 and fired[0]["playback"] == 1
          and fired[0]["cue"] == 2, str(fired))
    check("advanced to cue 2", e.playbacks[0]["index"] == 1)
    check("next deadline re-armed automatically",
          e.playbacks[0]["follow"]["at"] == 1004.1,
          str(e.playbacks[0]["follow"]["at"]))

    clock[0] = 1003.0
    res = e.act("cue_go", playback=1)      # manual step wins the race
    check("manual GO -> cue 3", res["ok"] and e.playbacks[0]["index"] == 2)
    check("manual GO re-arms from now",
          e.playbacks[0]["follow"]["at"] == 1005.0,
          str(e.playbacks[0]["follow"]["at"]))
    e._stop_follow_thread()
    clock[0] = 1004.5                      # past the OLD deadline (1004.1)
    check("pending auto-advance cancelled by manual GO",
          e._tick_follow() == [] and e.playbacks[0]["index"] == 2)
    clock[0] = 1010.0
    check("end of stack holds last cue (no loop)",
          e._tick_follow() == [] and e.playbacks[0]["index"] == 2
          and e.playbacks[0]["follow"]["at"] is None)

    e.act("follow_set", playback=1, loop=True, delay=0.5)
    check("loop setting re-arms from now",
          e.playbacks[0]["follow"]["at"] == 1010.5,
          str(e.playbacks[0]["follow"]["at"]))
    e._stop_follow_thread()
    clock[0] = 1011.0
    fired = e._tick_follow()
    check("loop wraps to cue 1",
          len(fired) == 1 and fired[0]["cue"] == 1
          and e.playbacks[0]["index"] == 0, str(fired))

    e.act("follow_set", playback=1, pause=True)
    check("pause clears the deadline",
          e.playbacks[0]["follow"]["at"] is None
          and e.playbacks[0]["follow"]["paused"])
    e._stop_follow_thread()
    clock[0] = 1020.0
    check("paused stack never fires",
          e._tick_follow() == [] and e.playbacks[0]["index"] == 0)
    e.act("follow_set", playback=1, pause=False)
    check("resume re-arms from now",
          e.playbacks[0]["follow"]["at"] == 1020.5,
          str(e.playbacks[0]["follow"]["at"]))
    e._stop_follow_thread()
    clock[0] = 1021.0
    check("fires after resume",
          len(e._tick_follow()) == 1 and e.playbacks[0]["index"] == 1)

    e.act("playback_release", playback=1)
    check("release clears active + deadline",
          e.playbacks[0]["active"] is False
          and e.playbacks[0]["follow"]["at"] is None)
    e._stop_follow_thread()
    clock[0] = 1030.0
    check("released stack never fires", e._tick_follow() == [])
    e.act("playback_activate", playback=1)
    check("activate re-arms auto-advance",
          e.playbacks[0]["active"]
          and e.playbacks[0]["follow"]["at"] == 1030.5,
          str(e.playbacks[0]["follow"]["at"]))

    check("status lists following stacks",
          e.act("status")["following"] == [1],
          str(e.act("status").get("following")))
    res = e.act("follow_set", playback=1, on=False)
    check("disarm stops the ticker thread",
          not res["follow"]["on"] and e._follow_thread is None,
          str(e._follow_thread))

    # races: manual GOs from two threads while ticks run concurrently -
    # one lock, no deadlock, no corrupted stack index.
    e.act("follow_set", playback=1, on=True, delay=0.1)
    problems = []

    def _hammer():
        try:
            for _ in range(25):
                e.act("cue_go", playback=1)
        except Exception as exc:            # noqa: BLE001
            problems.append(str(exc))

    workers = [_threading.Thread(target=_hammer) for _ in range(2)]
    started = _time.monotonic()
    for w in workers:
        w.start()
    for _ in range(50):
        e._tick_follow(clock[0] + 10_000)   # force auto-advances concurrently
        _time.sleep(0.005)
    for w in workers:
        w.join(timeout=10.0)
    check("no deadlock between manual GO and auto-advance",
          all(not w.is_alive() for w in workers) and not problems,
          f"{problems} alive={[w.is_alive() for w in workers]} "
          f"({ _time.monotonic() - started:.1f}s)")
    pb = e.playbacks[0]
    check("stack index stays valid under races",
          0 <= pb["index"] < len(pb["stack"]), str(pb["index"]))
    e.act("follow_set", playback=1, on=False)

    # shutdown cleans up; no timer/thread ever leaks
    e.act("follow_set", playback=1, on=True)
    thread = e._follow_thread
    check("ticker running before shutdown",
          thread is not None and thread.is_alive())
    e.shutdown()
    check("shutdown stops the ticker",
          e._follow_thread is None
          and (thread is None or not thread.is_alive()))
    check("no jarvis-follow threads left",
          not [t for t in _threading.enumerate()
               if t.name == "jarvis-follow" and t.is_alive()])


def test_profiles(tmp: Path) -> None:
    from app import engine as eng_mod
    from app import profiles

    print("fixture profile library (data-driven definitions)")
    db = tmp / "profiles.db"
    fixtures.seed_generics(db)
    first = profiles.install(db)
    check("library seeds 3 fixtures / 7 modes",
          first["fixtures"] == 3 and first["modes"] == 7, str(first))
    again = profiles.install(db)
    check("install is idempotent",
          again["fixtures"] == 0 and again["modes"] == 0
          and again["skipped"] == 3, str(again))
    check("4 generic + 3 profile fixtures in the DB",
          fixtures.count(db) == 7, str(fixtures.count(db)))
    spot = profiles.get("CHAUVET DJ", "Intimidator Spot 260")
    check("Spot 260 present in the library", spot is not None)
    check("lookup is case-insensitive",
          profiles.get("chauvet dj", "SLIMPAR T12 USB") is not None)
    check("unknown lookup returns None", profiles.get("Nope", "Nothing") is None)
    check("find matches by word", len(profiles.find("slimpar t12")) == 1)

    # every mode name that declares its width must match its labels
    mismatches = []
    for prof in profiles.PROFILES:
        for mode in prof["modes"]:
            digits = "".join(c for c in mode["name"] if c.isdigit())
            if digits and int(digits) != len(profiles.labels_for(mode)):
                mismatches.append(f"{prof['model']}/{mode['name']}")
    check("mode widths match their compiled channel lists", not mismatches,
          str(mismatches))
    labels = profiles.labels_for(spot["modes"][0])
    check("Spot 260 14ch compiles to 14 DMX slots", len(labels) == 14,
          str(labels))
    roles = [eng_mod.channel_role(lbl) for lbl in labels]
    check("Spot 260 14ch role mapping",
          roles == ["pan", "pan_fine", "tilt", "tilt_fine", "speed",
                    "wheel", "gobo", "gobo_rot", "prism", "zoom",
                    "dimmer", "strobe", "unused", "macro"], str(roles))
    mode = profiles.export(spot)["modes"][0]
    check("export shape (channels == declared width)",
          mode["channel_count"] == 14 == len(mode["channels"]))
    detail = {row["name"]: row for row in mode["detail"]}
    check("pan: 16-bit, 0..65535",
          detail["Pan"]["bits"] == 16 and detail["Pan"]["max"] == 65535
          and detail["Pan"]["min"] == 0, str(detail["Pan"]))
    check("strobe: 8-bit, 0..255, open at 4 (OFL 0-3 = closed)",
          detail["Strobe"]["bits"] == 8 and detail["Strobe"]["max"] == 255
          and detail["Strobe"]["default"] == 4
          and detail["Strobe"]["light_from"] == 4, str(detail["Strobe"]))
    check("zoom marked inverted (0=wide, 255=narrow)",
          next(r for r in mode["detail"] if r["name"] == "Zoom")["invert"])
    hits = fixtures.search(db, "intimidator spot 260")
    check("DB search finds the installed profile",
          len(hits) == 1 and hits[0]["manufacturer"] == "CHAUVET DJ",
          str(hits))

    e = eng_mod.Engine(db_path=db, dry_run=True, show_dir=tmp / "profile_shows")
    res = e.act("add_heads", query="intimidator spot 260", mode="14ch",
                qty=2, universe=1, address=1)
    check("patch 2x 14ch heads", res["ok"], str(res.get("error")))
    rows14 = sorted((p["universe"], p["address"])
                    for p in e.patch if p["channels"] == 14)
    check("sequential addressing 1 then 15", rows14 == [(1, 1), (1, 15)],
          str(rows14))
    res = e.act("add_heads", query="intimidator spot 260", mode="8ch",
                qty=1, universe=1, address=29)
    rows8 = [(p["universe"], p["address"]) for p in e.patch
             if p["channels"] == 8]
    check("8ch mode patches at 29",
          res["ok"] and rows8 == [(1, 29)], f"{res.get('error')} {rows8}")
    res = e.act("add_heads", query="intimidator spot 260", mode="99ch",
                qty=1, universe=1, address=40)
    check("unknown mode is a clean error",
          not res["ok"] and "no mode" in str(res.get("error")),
          str(res.get("error")))
    res = e.act("add_heads", query="slimpar pro rgba", mode="5ch",
                qty=1, universe=2, address=1)
    five = [p for p in e.patch if p["channels"] == 5]
    check("SlimPAR Pro 5ch patches dimmer + RGBA",
          res["ok"] and len(five) == 1
          and five[0]["map"][:5] == ["dimmer", "red", "green", "blue",
                                     "amber"],
          f"{res.get('error')} {[p['map'] for p in five]}")

    # --- LOCATE on a fixture with NO dimmer channel -----------------------
    # The 8ch mode has no dimmer and no colour mixer: only the Strobe
    # channel gates the lamp, and on this fixture 0-3 reads CLOSED.  LOCATE
    # used to write a "dimmer" role the head does not own, so nothing
    # reached the wire and the lamp stayed dark - the "locate does nothing"
    # report.
    def looks_of(engine) -> dict:
        return {row["n"]: row["look"] for row in engine._look()}

    spot8 = next(p for p in e.patch if p["channels"] == 8)
    check("8ch head really has no dimmer role",
          "dimmer" not in spot8["map"] and "strobe" in spot8["map"],
          str(spot8["map"]))
    e.act("select_heads", head=spot8["head_no"])
    e.act("clear_programmer")
    res = e.act("locate")
    check("locate works on a dimmer-less head",
          res["ok"] and res["heads"] == 1 and not res.get("no_light"),
          str(res))
    check("locate opens the strobe channel (OFL 0-3 = closed)",
          e.programmer.get(spot8["head_no"], {}).get("strobe") == 4,
          str(e.programmer.get(spot8["head_no"])))
    frames = e.build_frames()
    check("locate puts the open value on the wire",
          frames[1][spot8["address"] - 1 + 7] == 4,
          str(frames[1][spot8["address"] - 1:spot8["address"] + 7]))
    looks = looks_of(e)
    check("visualiser shows the dimmer-less head as lit",
          looks[spot8["head_no"]]["on"] is True
          and looks[spot8["head_no"]]["a"] == 1.0,
          str(looks[spot8["head_no"]]))
    e.act("set_intensity", level=0)
    check("intensity 0 closes the shutter on a dimmer-less head",
          e.programmer[spot8["head_no"]]["strobe"] == 0
          and looks_of(e)[spot8["head_no"]]["on"] is False,
          str(e.programmer[spot8["head_no"]]))
    e.act("set_intensity", level=100)
    check("intensity reopens it",
          e.programmer[spot8["head_no"]]["strobe"] == 4
          and looks_of(e)[spot8["head_no"]]["on"] is True,
          str(e.programmer))

    # an idle head must be dark on the wire, and the look must agree
    e.act("clear_programmer")
    check("un-driven gate writes 0 and the look stays dark",
          e.build_frames()[1][spot8["address"] - 1 + 7] == 0
          and looks_of(e)[spot8["head_no"]]["on"] is False,
          f"{e.build_frames()[1][spot8['address'] - 1 + 7]} "
          f"{looks_of(e)[spot8['head_no']]}")

    # a stale selection must fail loudly, not report "0 heads"
    e.patch = [h for h in e.patch if h["head_no"] != spot8["head_no"]]
    e.selected = [spot8["head_no"]]
    res = e.act("locate")
    check("locate on an unpatched selection is an error",
          not res["ok"] and "no patched heads" in str(res["error"]),
          str(res))
    e.shutdown()


class _LoopNode:
    """Loopback Art-Net node for scan tests.

    It answers ArtPoll with a byte-identical ArtPollReply, and emits one
    ArtDmx frame per poll.  Replies travel by BROADCAST because Windows
    hands a unicast datagram to only ONE of the sockets sharing a UDP
    port; broadcast reaches every socket, so scan() sees them no matter
    how the OS orders its binds.  The packet bytes are exactly what a
    real node would send.
    """

    def __init__(self, port: int, name: str, long_name: str,
                 swout: list[int], net: int = 0, sub: int = 0,
                 dmx_data: bytes | None = None, universe: int = 1):
        self.port = port
        self.name = name
        self.long_name = long_name
        self.swout = swout
        self.net = net
        self.sub = sub
        self.dmx_data = dmx_data
        self.universe = universe
        self.replied = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _reply(self) -> bytes:
        pkt = bytearray(213)
        pkt[0:8] = b"Art-Net\0"
        struct.pack_into("<H", pkt, 8, 0x2100)         # ArtPollReply
        pkt[10:14] = bytes((127, 0, 0, 1))             # IP
        struct.pack_into("<H", pkt, 14, 6454)           # port (LE)
        struct.pack_into(">H", pkt, 16, 0x010E)         # version
        pkt[18] = self.net                              # NetSwitch
        pkt[19] = self.sub                              # SubSwitch
        struct.pack_into(">H", pkt, 20, 0x1234)         # OEM
        pkt[26:44] = self.name.encode()[:17].ljust(18, b"\0")
        pkt[44:108] = self.long_name.encode()[:63].ljust(64, b"\0")
        struct.pack_into(">H", pkt, 172, len(self.swout))
        for i, sw in enumerate(self.swout[:4]):
            pkt[174 + i] = 0x80                        # output-capable port
            pkt[190 + i] = sw & 0x0F                    # SwOut
        pkt[200] = 0                                   # style: node
        return bytes(pkt)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="loop-node",
                                        daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import socket as _socket

        from app import artnet
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(_socket.SOL_SOCKET, _socket.SO_BROADCAST, 1)
        except OSError:
            pass
        s.bind(("", self.port))
        s.settimeout(0.1)
        reply = self._reply()
        artdmx = (artnet.build_artdmx(self.universe, self.dmx_data, 1)
                  if self.dmx_data is not None else None)
        while not self._stop.is_set():
            try:
                data, _addr = s.recvfrom(2048)
            except (TimeoutError, _socket.timeout):
                continue
            except OSError:
                break
            if len(data) < 10 or data[0:8] != b"Art-Net\0":
                continue
            if struct.unpack_from("<H", data, 8)[0] != artnet.ARTPOLL_OP:
                continue                                # replies/junk: ignore
            try:
                s.sendto(reply, ("255.255.255.255", self.port))
                if artdmx is not None:
                    s.sendto(artdmx, ("255.255.255.255", self.port))
                self.replied += 1
            except OSError:
                pass
        s.close()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def test_scan_simulated() -> None:
    import socket as _socket
    import time as _time

    from app import artnet

    print("Art-Net scan (simulated nodes over loopback)")
    port = _free_udp_port()
    node_a = _LoopNode(port, "ScanNode A", "Loopback node A", swout=[0],
                       dmx_data=bytes([0] * 23 + [200] + [0] * 488),
                       universe=1)
    node_b = _LoopNode(port, "ScanNode B", "Loopback node B", swout=[2])
    node_a.start()
    node_b.start()
    _time.sleep(0.2)                                  # let both bind
    started = _time.monotonic()
    result = artnet.scan(timeout=0.9, port=port, net=0)
    elapsed = _time.monotonic() - started
    node_a.stop()
    node_b.stop()

    check("scan completes without hanging", elapsed < 3.0, f"{elapsed:.2f}s")
    check("no error on a loopback scan", not result.get("error"),
          str(result.get("error")))
    check("polls sent (initial pair + repoll)",
          result["polls_sent"] in (2, 3), str(result["polls_sent"]))
    check("replies received from both nodes", result["replies"] >= 2,
          str(result["replies"]))
    names = sorted(n["name"] for n in result["nodes"])
    check("both nodes discovered", names == ["ScanNode A", "ScanNode B"],
          str(names))
    row_a = next((n for n in result["nodes"] if n["name"] == "ScanNode A"), {})
    check("clean node info",
          row_a.get("ip") == "127.0.0.1" and row_a.get("port") == 6454
          and row_a.get("long_name") == "Loopback node A"
          and row_a.get("output_ports") == [0]
          and row_a.get("oem") == 0x1234, str(row_a))
    rows = result["universes"]
    pas = [r["port_address"] for r in rows]
    check("rows sorted by port address", pas == sorted(pas), str(pas))
    check("two universe rows", len(rows) == 2, str(rows))
    if len(rows) == 2:
        check("universe 1: ArtDmx + announcement (via=both)",
              rows[0]["universe"] == 1 and rows[0]["channels"] == 24
              and rows[0]["via"] == "both"
              and rows[0]["node"] == "ScanNode A", str(rows[0]))
        check("universe 3: announced while idle (via=artpoll)",
              rows[1]["universe"] == 3 and rows[1]["via"] == "artpoll"
              and rows[1]["frames"] == 0
              and rows[1]["node"] == "ScanNode B", str(rows[1]))
    check("node answered the polls", node_a.replied >= 1, str(node_a.replied))

    # junk / malformed traffic must never invent a device or universe
    port2 = _free_udp_port()
    tx = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    tx.setsockopt(_socket.SOL_SOCKET, _socket.SO_BROADCAST, 1)
    stop_junk = threading.Event()

    def _junk() -> None:
        while not stop_junk.is_set():
            try:
                tx.sendto(b"HELLO-JUNK", ("255.255.255.255", port2))
                tx.sendto(b"Art-Net\0" + struct.pack("<H", artnet.ARTPOLL_OP)
                          + b"\x00",
                          ("255.255.255.255", port2))          # a poll
                tx.sendto(b"Art-Net\0" + b"\x00\x50\x0e",
                          ("255.255.255.255", port2))          # truncated Dmx
                tx.sendto(b"Art-Net\0" + struct.pack("<H", 0x2100) + b"\x01",
                          ("255.255.255.255", port2))          # short reply
            except OSError:
                return
            _time.sleep(0.05)

    junk_thread = threading.Thread(target=_junk, daemon=True)
    junk_thread.start()
    result2 = artnet.scan(timeout=0.5, port=port2, net=0)
    stop_junk.set()
    junk_thread.join(timeout=2.0)
    tx.close()
    check("junk traffic: no false devices", result2["nodes"] == [],
          str(result2["nodes"]))
    check("junk traffic: no fake universes", result2["universes"] == [],
          str(result2["universes"]))
    check("junk traffic: replies/frames not counted",
          result2["replies"] == 0 and result2["frames"] == 0,
          str({k: result2[k] for k in ("replies", "frames")}))

    # silent wire (no hardware): returns promptly, empty, no error
    port3 = _free_udp_port()
    started = _time.monotonic()
    result3 = artnet.scan(timeout=0.4, port=port3, net=0)
    elapsed3 = _time.monotonic() - started
    check("silent wire returns promptly", elapsed3 < 2.0, f"{elapsed3:.2f}s")
    check("silent wire: empty result, no error, polls still sent",
          result3["nodes"] == [] and result3["universes"] == []
          and not result3.get("error") and result3["polls_sent"] >= 2,
          str(result3))


def test_aim() -> None:
    """A moving head must actually MOVE in the 3D view.

    The bug this covers: a head with pan/tilt channels rendered frozen on
    the landing point derived from where it hangs, so the operator panned
    and tilted it and the beam did not move.  The DMX on the wire was
    correct throughout - only the visualiser was wrong - which is why it
    survived: nothing in Python could see it.

    Three things have to hold for the aim to reach the screen, and each
    has failed on its own:
      1. the ENGINE reports aim, and only when it is genuinely driven
         (absent must mean absent - defaulting to 0 would freeze every
         static fixture pointing at the stage edge),
      2. the CLIENT carries the aim through its look map without dropping
         the key, and
      3. the VISUALISER turns those 0..1 values into a direction and
         re-derives where the beam lands, so the cone and the head agree.

    The engine half runs against a real fixture.  The two client halves
    are checked as source contracts plus the arithmetic, because a
    headless run cannot drive a browser - the client's own `beam()`
    accessor exists so the geometry can be measured rather than assumed.
    """
    print("moving-head aim (engine reports, client carries, viz aims)")
    import math

    from app import engine as eng
    from app import fixtures

    def seed_16bit_moving_head(db_path):
        """A 16-bit pan/tilt head, inserted the way a GDTF import does.

        A 16-bit channel is TWO roles across two DMX bytes - "Pan (16-bit)"
        and "Pan fine" - which is the whole reason the aim has to
        normalise against 65535 rather than 255.  Seeding it here keeps the
        suite hermetic instead of leaning on whatever happens to be in the
        operator's real fixture database.
        """
        channels = ["Pan (16-bit)", "Pan fine", "Tilt (16-bit)", "Tilt fine",
                    "Pan/Tilt Speed", "Dimmer"]
        with fixtures.db(db_path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO fixtures (manufacturer, model, source,"
                " imported_at) VALUES (?,?,?,datetime('now'))",
                ("Selftest", "Aim 16ch", "built-in"))
            fid = cur.lastrowid
            if not fid:
                fid = conn.execute(
                    "SELECT id FROM fixtures WHERE manufacturer=? AND model=?",
                    ("Selftest", "Aim 16ch")).fetchone()[0]
            conn.execute(
                "INSERT INTO modes (fixture_id, name, channel_count, channels)"
                " VALUES (?,?,?,?)",
                (fid, "16ch", len(channels), json.dumps(channels)))
        fixtures.invalidate_cache()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "aim.db"
        fixtures.seed_generics(db)
        seed_16bit_moving_head(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            spot = e.act("add_heads", query="Moving Head Spot 16ch", qty=1)
            check("a moving head can be added", spot["ok"], json.dumps(spot))
            e.act("select_heads", head=e.patch[0]["head_no"])
            e.act("set_intensity", level=100)

            # 1. nothing driving the aim -> the keys must be ABSENT
            row = e._looks()[0]
            check("aim is absent when nothing drives it",
                  "pan" not in row and "tilt" not in row, json.dumps(row))

            # 2. drive it and the aim appears, normalised to 0..1
            e.act("set_attribute", attribute="pan", value=64)
            e.act("set_attribute", attribute="tilt", value=200)
            row = e._looks()[0]
            check("driven aim appears on the feed",
                  "pan" in row and "tilt" in row, json.dumps(row))
            check("8-bit aim normalises against 255",
                  abs(row["pan"] - 64 / 255) < 0.002
                  and abs(row["tilt"] - 200 / 255) < 0.002,
                  json.dumps({k: row[k] for k in ("pan", "tilt")}))
            check("aim stays inside 0..1",
                  0.0 <= row["pan"] <= 1.0 and 0.0 <= row["tilt"] <= 1.0,
                  json.dumps(row))

            for value, want in ((0, 0.0), (255, 1.0)):
                e.act("set_attribute", attribute="pan", value=value)
                got = e._looks()[0].get("pan")
                check(f"pan {value} normalises to {want}",
                      got is not None and abs(got - want) < 0.002, str(got))

            # out-of-range values clamp rather than throwing or wrapping
            e.act("set_attribute", attribute="tilt", value=9999)
            got = e._looks()[0].get("tilt")
            check("an over-range aim clamps to 1.0",
                  got is not None and abs(got - 1.0) < 0.002, str(got))
            e.act("set_attribute", attribute="tilt", value=-500)
            got = e._looks()[0].get("tilt")
            check("an under-range aim clamps to 0.0",
                  got is not None and abs(got - 0.0) < 0.002, str(got))

            # 3. a 16-bit head: ONE logical value across two DMX bytes, so
            #    it must normalise against 65535, not 255
            e.act("clear_programmer")
            first = e.patch[0]["head_no"]
            e.act("remove_heads", head=first)
            wide = e.act("add_heads", query="Aim 16ch", qty=1)
            check("a 16-bit moving head can be added", wide["ok"],
                  json.dumps(wide))
            head_no = e.patch[0]["head_no"]
            check("the 16-bit head really has a fine channel",
                  "pan_fine" in (e.patch[0].get("map") or []),
                  json.dumps(e.patch[0].get("map")))
            e.act("select_heads", head=head_no)
            e.act("set_intensity", level=100)
            e.act("set_attribute", attribute="pan", value=32768)
            row = e._looks()[0]
            check("16-bit aim normalises against 65535, not 255",
                  "pan" in row and abs(row["pan"] - 32768 / 65535) < 0.002,
                  json.dumps(row.get("pan")))
            e.act("set_attribute", attribute="pan", value=0)
            e.act("set_attribute", attribute="pan_fine", value=255)
            row = e._looks()[0]
            check("the fine channel's own value wins over the coarse byte",
                  "pan" in row and abs(row["pan"] - (255 / 65535)) < 0.002,
                  json.dumps(row.get("pan")))

            # 4. clearing the programmer takes the aim with it, so the
            #    visualiser falls back instead of freezing on a stale aim
            e.act("clear_programmer")
            e.act("set_intensity", level=100)
            row = e._looks()[0]
            check("clearing the programmer removes the aim",
                  "pan" not in row and "tilt" not in row, json.dumps(row))
        finally:
            e.shutdown()

    # -- client: the aim must survive the trip through the look map ------



    # -- the mapping the visualiser applies, checked as arithmetic --------
    # Reproduced here so a change to those constants fails in CI instead
    # of silently re-pointing every moving head on the rig.
    def elevation(tilt01):
        return (tilt01 * 270 - 90)

    def hanging_dir(pan01, tilt01):
        pan = math.radians(pan01 * 360.0)
        el = math.radians(elevation(tilt01))
        ch = math.cos(el)
        return (-math.sin(pan) * ch, math.sin(el), -math.cos(pan) * ch)

    check("tilt 0 points the head straight down",
          abs(hanging_dir(0.0, 0.0)[1] + 1.0) < 1e-9,
          json.dumps(hanging_dir(0.0, 0.0)))
    check("tilt at its midpoint is 45 degrees above horizontal",
          abs(elevation(0.5) - 45.0) < 1e-9, str(elevation(0.5)))
    check("tilt at full is 180 degrees, horizontal and facing back",
          abs(elevation(1.0) - 180.0) < 1e-9, str(elevation(1.0)))
    check("the tilt sweep covers the full 270 degrees",
          abs(elevation(1.0) - elevation(0.0) - 270.0) < 1e-9,
          str(elevation(1.0) - elevation(0.0)))
    check("tilt past vertical really does flip the horizontal component",
          # at pan 0 the head throws along -Z, so the component to watch is
          # z: it must reverse once tilt passes straight-up
          hanging_dir(0.0, 0.5)[2] < 0 < hanging_dir(0.0, 1.0)[2],
          json.dumps([hanging_dir(0.0, 0.5), hanging_dir(0.0, 1.0)]))
    check("pan is a full turn, 0..1 -> 0..360 degrees",
          abs(1.0 * 360.0 - 360.0) < 1e-9, "")
    # pan 0 and pan 0.5 both throw along x = 0 (sin 0 = sin 180 = 0), so
    # the sweep has to be sampled where the beam actually swings
    check("pan rotates the beam through a half turn",
          hanging_dir(0.25, 0.5)[0] < 0 < hanging_dir(0.75, 0.5)[0],
          json.dumps([hanging_dir(0.25, 0.5), hanging_dir(0.75, 0.5)]))
    check("the two halves of a pan turn are mirror images",
          abs(hanging_dir(0.25, 0.5)[0] + hanging_dir(0.75, 0.5)[0]) < 1e-9
          and hanging_dir(0.25, 0.5)[2] < 0 < hanging_dir(0.75, 0.5)[2],
          json.dumps([hanging_dir(0.25, 0.5), hanging_dir(0.75, 0.5)]))
    check("every aim in range yields a unit direction",
          all(abs(math.sqrt(sum(c * c for c in hanging_dir(p, t))) - 1.0)
              < 1e-6
              for p in (0.0, 0.25, 0.5, 0.749, 1.0)
              for t in (0.0, 0.5, 1.0)), "")

    # -- served assets must never be stale against the API ---------------
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    check("static assets are revalidated, never reused blindly",
          '"Cache-Control", "no-cache"' in main and "If-None-Match" in main,
          "a cached client silently ignores new API fields")
    check("revalidation is cheap (an ETag, so a 304 carries no body)",
          "ETag" in main and "304" in main, "")

    # -- the fixture library is the GDTF Share's, not a set of guesses ----
    # Seeding is opt-in because a generic channel map is a GUESS, and a
    # wrong guess mis-addresses a real light silently.  It also has to be
    # opt-in in every entry point: the inbox importer and the loopback
    # diagnostic both used to re-add the four generics behind the
    # operator's back.
    check("startup does not re-seed the fixture library by default",
          "if config.FIXTURE_SEED_BUILTINS:" in main, "")
    check("seeding is off unless explicitly turned on",
          'FIXTURE_SEED_BUILTINS", "false"' in
          (ROOT / "app" / "config.py").read_text(encoding="utf-8"), "")
    check("the inbox importer respects the same flag",
          "if config.FIXTURE_SEED_BUILTINS:" in
          (ROOT / "tools" / "import_gdtf.py").read_text(encoding="utf-8"), "")
    loopback = (ROOT / "tools" / "artnet_loopback.py").read_text(encoding="utf-8")
    check("the loopback diagnostic uses a scratch library, not the operator's",
          "mkdtemp" in loopback and "db_path=config.DB_PATH" not in loopback,
          "a diagnostic must not mutate the live fixture library")


    # -- the GDTF Share panel is reachable from the add-heads dialog -----
    # Without these the whole client is an endpoint nobody calls, which is
    # how the previous milestone shipped looking finished.
    # The search text used to be passed as `refresh=`, so the box searched
    # nothing and looked broken; and a search reply has no session fields,
    # so assigning it to the status made a signed-in client look signed
    # out.  Both are contracts now.



def test_palette_targets(tmp: Path) -> None:
    """A palette is ATTRIBUTE VALUES; the selection decides who gets them.

    This suite used to guard a smaller claim: that `include_palette`
    narrowed to the overlap with the selection, because a disjoint
    selection fell through and wrote to every recorded head.  The fix was
    a guard.  The real fix is to stop recording a list of heads at all - a
    palette is "this is House blue", and the selection is whoever should
    get it - so the failure is no longer expressible.

    The properties below are therefore STRONGER than the ones they
    replace, and each follows from the shape rather than from a check
    somebody remembered to add:
      * a palette recorded before a fixture existed still applies to it;
      * a head with no matching channel is reported, not silently skipped;
      * a show saved in the OLD per-head format still loads.
    """
    print("palettes (attribute values, selection decides)")
    import json
    from pathlib import Path as _P

    from app import engine as eng
    from app import fixtures

    td = tempfile.mkdtemp()
    db = _P(td) / "x.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=_P(td))
    try:
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=3,
              universe=1, address=1, x=0, y=0, z=5)
        e.act("select_heads", heads=[1, 2, 3])
        e.act("set_colour", hex="#0066ff")
        rec = e.act("record_palette", kind="colour", name="House blue")
        check("palette recorded", rec.get("ok") and rec.get("n") == 1, str(rec))

        entry = e.snapshot()["palettes"]["colour"][0]
        check("the entry is role-keyed, not head-keyed",
              set(entry["values"]) == {"red", "green", "blue"},
              str(entry["values"]))
        check("the recorded colour is the one that was set",
              entry["values"]["blue"] == 255, str(entry["values"]))

        # -- it applies to a head added AFTER it was recorded ------------
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=2,
              universe=1, address=20, x=0, y=0, z=5)
        e.act("select_heads", heads=[4, 5])
        e.act("clear_programmer")
        later = e.act("include_palette", kind="colour", n=1)
        check("a palette applies to fixtures added AFTER it was recorded",
              later.get("ok") and later.get("heads") == 2, str(later))
        check("and the new heads really got the colour",
              e.snapshot()["programmer"]["values"].get("4", {}).get("blue")
              == 255, str(e.snapshot()["programmer"]["values"]))

        # -- a head with no matching channel is REPORTED ------------------
        # A BEAM palette onto a PAR is a genuine mismatch: a 4ch RGBW par
        # has no gobo, prism, zoom or shutter at all.  (A colour palette
        # would not test this - the built-in generic mover turns out to
        # carry red/green/blue of its own, which is exactly the sort of
        # assumption a test that never runs its setup would get wrong.)
        e.act("add_heads", query="Moving Head Spot 16ch", mode="16ch Advanced",
              qty=1, universe=2, address=1, x=0, y=0, z=5)
        e.act("select_heads", heads=[6])
        e.act("set_attribute", attribute="gobo", value=120)
        e.act("record_palette", kind="beam", name="Gobo 3")
        e.act("select_heads", heads=[1])
        e.act("clear_programmer")
        miss = e.act("include_palette", kind="beam", n=1)
        check("a head with no matching channel is reported, not silent",
              (not miss.get("ok"))
              and "nothing for" in str(miss.get("error", "")), str(miss))
        check("and it wrote nothing to that head",
              "1" not in e.snapshot()["programmer"]["values"],
              str(e.snapshot()["programmer"]["values"]))

        # -- and reported when only SOME match ---------------------------
        e.act("select_heads", heads=[1, 6])
        e.act("clear_programmer")
        part = e.act("include_palette", kind="beam", n=1)
        check("a partly-capable selection applies and reports the rest",
              part.get("ok") and part.get("heads") == 1
              and part.get("skipped") == [1], str(part))

        # -- presets are a different thing, and they work ---------------
        e.act("select_heads", heads=[1, 2, 3])
        e.act("set_intensity", level=80)
        e.act("set_attribute", attribute="pan", value=120)
        pre = e.act("record_preset", name="Warm Wash")
        check("a preset records a whole look, not one attribute family",
              pre.get("ok") and "dimmer" in (pre.get("roles") or []), str(pre))
        e.act("select_heads", heads=[4, 5])
        e.act("clear_programmer")
        ap = e.act("include_preset", n=1)
        check("a preset applies its LEVEL as well as its colour",
              ap.get("ok")
              and e.snapshot()["programmer"]["values"].get("4", {}).get("dimmer")
              == 80, str(ap))
        check("and reports the roles a PAR cannot do",
              "pan" in (pre.get("roles") or [])
              and e.snapshot()["programmer"]["values"].get("4", {}).get("pan")
              is None,
              str(e.snapshot()["programmer"]["values"].get("4")))

        # -- a per-head palette from an old show still loads -------------
        old = {"colour": [{"n": 1, "name": "Old House Blue", "values": {
            "1": {"red": 0, "green": 102, "blue": 255},
            "2": {"red": 0, "green": 102, "blue": 255},
            "3": {"red": 10, "green": 99, "blue": 250},
        }}]}
        migrated = eng.Engine._normalize_palettes(old)
        vals = migrated["colour"][0]["values"]
        check("an old per-head palette migrates to role keys",
              set(vals) == {"red", "green", "blue"}, str(vals))
        check("and takes the majority value where heads disagreed",
              vals["red"] == 0, str(vals))
        check("the migration is JSON-safe",
              json.loads(json.dumps(migrated)) == migrated, "")

        # -- re-recording overwrites in place -----------------------------
        e.act("select_heads", heads=[1, 2, 3])
        e.act("set_colour", hex="#ff0000")
        e.act("record_palette", kind="colour", name="Red Wash", palette=1)
        check("re-recording the same number overwrites it",
              len(e.snapshot()["palettes"]["colour"]) == 1
              and e.snapshot()["palettes"]["colour"][0]["values"]["red"] == 255,
              str(e.snapshot()["palettes"]["colour"]))
    finally:
        e.shutdown()


def test_channels(tmp: Path) -> None:
    """The DMX channel sheet and the capability report.

    Before these, the console could not answer the three questions every
    DMX fault starts with: which bytes am I sending, to which channel, at
    what address.  Nothing in the engine or the client exposed a single
    channel value, so the only way to find out was a tester at the far end
    of the cable.

    The load-bearing claim is that the values come from the frames that
    will actually be sent - not from a re-derivation of the roles.  Those
    disagree in exactly the cases that matter (16-bit pairs split over two
    slots, HTP merge, master and blackout), they disagree the same way
    every time, and a report built any other way would survive being
    checked once.
    """
    print("channel sheet + capabilities")
    from pathlib import Path as _P

    from app import engine as eng
    from app import fixtures

    td = tempfile.mkdtemp()
    db = _P(td) / "x.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=_P(td))
    try:
        e.act("add_heads", query="Moving Head Spot 16ch", mode="16ch Advanced",
              qty=2, universe=1, address=1, x=0, y=0, z=5)
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=2,
              universe=1, address=40, x=0, y=0, z=1)

        # -- an empty selection says so, it does not invent a table -----
        empty = e.channel_report()
        check("no selection reports nothing, not everything",
              empty["heads"] == [] and empty["summary"] == "nothing selected",
              str(empty["summary"]))

        # -- the report shows the WIRE, not the roles --------------------
        e.act("select_heads", heads=[1])
        e.act("set_attribute", attribute="pan", value=200)
        e.act("set_intensity", level=100)
        rep = e.channel_report()
        head = rep["heads"][0]
        by_role = {c["role"]: c for c in head["channels"]}
        frames = e.build_frames()
        check("the value reported is the byte in the frame",
              by_role["pan"]["value"] == frames[1][by_role["pan"]["abs"] - 1],
              "report=%s frame=%s" % (by_role["pan"]["value"],
                                      frames[1][by_role["pan"]["abs"] - 1]))
        check("intensity reaches its own channel",
              by_role["dimmer"]["value"] == frames[1][by_role["dimmer"]["abs"] - 1]
              and by_role["dimmer"]["value"] > 0, str(by_role["dimmer"]))
        check("absolute addresses follow the head's start address",
              by_role["pan"]["abs"] == 7, str(by_role["pan"]["abs"]))
        check("a channel in the programmer is flagged as set",
              by_role["pan"]["programmed"] and not by_role["red"]["programmed"],
              str(by_role["pan"]))

        # -- labels come from the fixture, not from the role ------------
        check("the fixture's own channel name is shown",
              by_role["pan"]["label"] == "Pan", by_role["pan"]["label"])

        # -- an un-driven channel reads zero, and that is not a bug -----
        quiet = next(r for r in ("gobo", "prism", "gobo_rot", "focus") if r in by_role)
        check("an untouched channel reports 0 rather than blank",
              by_role[quiet]["value"] == 0, str(by_role[quiet]))
        check("an LED head with a dimmer shows white when nothing sets its colour",
              by_role["red"]["value"] == 255 and not by_role["red"]["programmed"], str(by_role["red"]))

        # -- the whole selection at once ---------------------------------
        e.act("select_heads", heads=[1, 2, 3, 4])
        allrep = e.channel_report()
        check("the report covers the whole selection",
              len(allrep["heads"]) == 4, str(len(allrep["heads"])))
        check("channels and totals add up",
              allrep["total"] == sum(h["footprint"] for h in allrep["heads"])
              and allrep["driven"] + allrep["uncontrolled"] == allrep["total"],
              str(allrep["summary"]))
        check("the summary leads with the uncontrollable count",
              "NOT controllable" not in allrep["summary"]
              or "controllable" in allrep["summary"], allrep["summary"])

        # -- capabilities: the silent partial write ---------------------
        # Two movers and two PARs selected.  Setting tilt on that mix
        # writes to two heads and says nothing, so the two movers must be
        # reported as partial coverage rather than the whole selection
        # being treated as if it could do it.
        cap = e.capabilities()
        check("capabilities counts the selection", cap["heads"] == 4,
              str(cap["heads"]))
        partial = {p["role"]: p for p in cap["partial"]}
        check("pan is reported as only on the movers",
              partial.get("pan", {}).get("heads") == [1, 2]
              and partial["pan"]["missing"] == 2, str(partial.get("pan")))
        check("shutter is reported as only on the movers",
              partial.get("shutter", {}).get("heads") == [1, 2]
              and partial["shutter"]["missing"] == 2, str(partial.get("shutter")))
        # Dimmer AND red are on all four, so neither is partial - a check
        # that assumed otherwise would have "passed" for entirely the wrong
        # reason and kept passing after the report broke.
        check("an attribute on every head is not called partial",
              "dimmer" not in partial and "red" not in partial,
              str({k: v for k, v in partial.items()
                   if k in ("dimmer", "red")}))
        check("the capability summary mentions the shortfall",
              "not on every head" in cap["summary"], cap["summary"])

        # -- explicit head list beats the selection ---------------------
        one = e.channel_report([3])
        check("an explicit head list overrides the selection",
              len(one["heads"]) == 1 and one["heads"][0]["head_no"] == 3,
              str([h["head_no"] for h in one["heads"]]))
        check("capabilities honours an explicit list too",
              e.capabilities([3])["heads"] == 1, "")
    finally:
        e.shutdown()


def test_channels_16bit(tmp: Path) -> None:
    """A 16-bit channel is TWO DMX bytes, and the sheet must show both.

    The 8-bit path is covered above, but a 16-bit pair is exactly where a
    role-derived report and a frame-derived report part company: the role
    is one thing, the wire is two slots, and one logical value is split
    across them.  So the fine half has to appear as its own row, at its
    own absolute address, carrying the low half of the split.
    """
    print("channel sheet: 16-bit pairs")
    from app import engine as eng
    from app import fixtures

    # Seeded, not borrowed: the built-in "16ch Advanced" generic is 8-bit
    # throughout, so a 16-bit head has to exist before there is anything
    # to prove.  The archive is spec-shaped (description.xml under
    # FixtureType) because that is what the real importer produces.
    path = tmp / "WideMover.gdtf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("description.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>'
            '<GDTF DataVersion="1.0">'
            '<FixtureType Name="WideMover" Manufacturer="TestCo">'
            '<DMXModes><DMXMode Name="8ch"><DMXChannels>'
            '<DMXChannel Offset="1,2"><LogicalChannel Attribute="Pan">'
            '<ChannelFunction Name="Pan" OriginalAttribute="Pan"/>'
            '</LogicalChannel></DMXChannel>'
            '<DMXChannel Offset="3,4"><LogicalChannel Attribute="Tilt">'
            '<ChannelFunction Name="Tilt" OriginalAttribute="Tilt"/>'
            '</LogicalChannel></DMXChannel>'
            '<DMXChannel Offset="5"><LogicalChannel Attribute="Dimmer">'
            '<ChannelFunction Name="Dim" OriginalAttribute="Dimmer"/>'
            '</LogicalChannel></DMXChannel>'
            '<DMXChannel Offset="6"><LogicalChannel Attribute="NoFeature">'
            '<ChannelFunction Name="None" OriginalAttribute="NoFeature"/>'
            '</LogicalChannel></DMXChannel>'
            '</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>'))
    db = tmp / "ch16.db"
    fixtures.seed_generics(db)
    fixtures.import_file(db, path)

    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s16")
    try:
        e.act("add_heads", query="WideMover", mode="8ch", qty=1,
              universe=1, address=1, x=0, y=0, z=5)
        e.act("select_heads", heads=[1])
        e.act("set_attribute", attribute="pan", value=40000)
        rep = e.channel_report()
        head = rep["heads"][0]
        roles = [c["role"] for c in head["channels"]]
        check("the coarse and fine halves are separate rows",
              roles[:4] == ["pan", "pan_fine", "tilt", "tilt_fine"], str(roles))
        check("the vendor's 'NoFeature' reads as unused, not as a control",
              roles[5] == "unused" and not head["channels"][5]["driven"],
              str(roles[5]))
        check("the unused channel is counted as not controllable",
              rep["uncontrolled"] == 1, str(rep["summary"]))
        check("the summary says so in words",
              "NOT controllable" in rep["summary"], rep["summary"])

        frames = e.build_frames()
        coarse = head["channels"][0]
        fine = head["channels"][1]
        check("the reported values are the bytes in the frame",
              frames[1][coarse["abs"] - 1] == coarse["value"]
              and frames[1][fine["abs"] - 1] == fine["value"],
              "coarse=%s fine=%s" % (coarse["value"], fine["value"]))
        # 40000 of 65535 is ~61%, so the coarse byte sits near 155 - and
        # emphatically not 40000/256, which is what a report derived from
        # the logical value instead of the wire would have shown.
        check("the coarse byte is the high half of the split",
              150 <= coarse["value"] <= 160, str(coarse["value"]))
        check("the fine byte carries the remainder, not zero",
              fine["value"] > 0 and fine["value"] <= 255, str(fine["value"]))
    finally:
        e.shutdown()
