"""Self-test suites, part 1: gdtf, gdtf_spec, db, showdesign, artnet, artnet_discovery, channel_roles, engine, ...."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app import config, fixtures
from tools import import_gdtf
from tools.selftests.common import SPEC_GDTF, SYNTHETIC_GDTF, _valueerror, check


def test_gdtf(tmp: Path) -> None:
    print("GDTF parser")
    gdtf_path = tmp / "TestBrand-Beam400.gdtf"
    with zipfile.ZipFile(gdtf_path, "w") as zf:
        zf.writestr("GDTF", SYNTHETIC_GDTF)

    parsed = fixtures.parse_gdtf(gdtf_path)
    check("one fixture parsed", len(parsed) == 1, str(parsed))
    fix = parsed[0]
    check("manufacturer", fix["manufacturer"] == "TestBrand", fix["manufacturer"])
    check("model", fix["model"] == "Beam400", fix["model"])
    check("two modes", len(fix["modes"]) == 2, str(len(fix["modes"])))

    basic = next(m for m in fix["modes"] if m["name"] == "Basic 8ch")
    check("8ch footprint with 16-bit pan", basic["channel_count"] == 8, str(basic["channel_count"]))
    chans = basic["channels"]
    check("channel 1 = Dimmer", chans[0] == "Dimmer", chans[0])
    check("channel 3 = Red", chans[2] == "Red", chans[2])
    check("channel 6 = Pan (16-bit)", chans[5].startswith("Pan"), chans[5])
    check("channel 8 = Tilt", chans[7] == "Tilt", chans[7])

    mini = next(m for m in fix["modes"] if m["name"] == "Mini 4ch")
    check("4ch mode", mini["channel_count"] == 4, str(mini["channel_count"]))


def test_gdtf_spec(tmp: Path) -> None:
    """A GDTF 1.0 file shaped the way gdtf-share.com actually serves one.

    Every check here corresponds to a silent wrong answer that a live
    download produced, and none of them raise - the importer "succeeded"
    while writing a fixture called after its own filename with a single
    zero-channel mode.  A self-test built on a hand-made archive could
    never have found that, because the hand-made archive used the entry
    name and element layout the parser happened to expect.
    """
    print("GDTF 1.0 spec layout (description.xml + FixtureType attributes)")
    # Its OWN folder, outside the shared `tmp`: import_directory walks a
    # folder RECURSIVELY, so any .gdtf left in `tmp` silently changes
    # another suite's scanned/imported counts.
    import shutil as _shutil
    import tempfile as _tempfile
    here = Path(_tempfile.mkdtemp(prefix="gdtf-spec-"))
    try:
        path = here / "real-world.gdtf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("description.xml", SPEC_GDTF)
            # Real archives carry these; none may be mistaken for the document.
            zf.writestr("2020-10-23.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
            zf.writestr("models/3ds/Body.3ds", b"3ds\x00" + b"\x00" * 32)

        parsed = fixtures.parse_gdtf(path)
        check("one fixture parsed", len(parsed) == 1, str(len(parsed)))
        fix = parsed[0]
        check("manufacturer comes from the FixtureType attribute",
              fix["manufacturer"] == "Acme Lighting", fix["manufacturer"])
        check("model comes from the FixtureType attribute, not the filename",
              fix["model"] == "Beam900", fix["model"])
        check("the model is not the archive name", fix["model"] != path.stem,
              fix["model"])
        check("both modes were read from inside FixtureType",
              len(fix["modes"]) == 2, str([m["name"] for m in fix["modes"]]))

        wide = next(m for m in fix["modes"] if m["name"] == "6 Channel")
        check("the 16-bit pairs widen the footprint to 6",
              wide["channel_count"] == 6, str(wide["channel_count"]))
        check("16-bit pan/tilt keep their coarse/fine pairing",
              wide["channels"][0] == "Pan (16-bit)" and
              wide["channels"][1] == "Pan fine" and
              wide["channels"][2].startswith("Tilt") and
              wide["channels"][3] == "Tilt fine", str(wide["channels"][:5]))
        check("the vendor's own channel name wins over the generic attribute",
              wide["channels"][4] == "Red", str(wide["channels"][4]))

        # A spec file must not be confused by a sibling that is also XML-ish.
        other = here / "with-junk.gdtf"
        with zipfile.ZipFile(other, "w") as zf:
            zf.writestr("wheels/Gobo 1.xml", "<notgdtf/>")
            zf.writestr("description.xml", SPEC_GDTF)
        check("a non-GDTF sibling entry is not parsed as the document",
              fixtures.parse_gdtf(other)[0]["model"] == "Beam900", "")

        # A legacy entry name must still work, so older exports keep importing.
        legacy = here / "legacy.gdtf"
        with zipfile.ZipFile(legacy, "w") as zf:
            zf.writestr("GDTF", SYNTHETIC_GDTF)
        check("a legacy 'GDTF' entry name still parses",
              fixtures.parse_gdtf(legacy)[0]["model"] == "Beam400", "")

        empty = here / "empty.gdtf"
        with zipfile.ZipFile(empty, "w") as zf:
            zf.writestr("readme.txt", "nothing here")
        try:
            fixtures.parse_gdtf(empty)
            check("an archive with no document is an error", False, "no raise")
        except ValueError as exc:
            check("an archive with no document is an error",
                  "no GDTF document" in str(exc), str(exc))
    finally:
        _shutil.rmtree(here, ignore_errors=True)


def test_db(tmp: Path) -> None:
    print("database")
    db = tmp / "test.db"
    fixtures.seed_generics(db)
    gdtf_path = tmp / "TestBrand-Beam400.gdtf"
    fixtures.import_file(db, gdtf_path)

    check("generics + imported present", fixtures.count(db) == 5, str(fixtures.count(db)))
    hits = fixtures.search(db, "beam")
    check("search by model", len(hits) == 1 and hits[0]["model"] == "Beam400", str(hits))
    hits = fixtures.search(db, "TestBrand")
    check("search by brand", len(hits) == 1, str(len(hits)))
    full = fixtures.get(db, hits[0]["id"])
    check("get_fixture modes", len(full["modes"]) == 2, str(full["modes"]))
    summary = import_gdtf.run(db, tmp)
    check("import_directory idempotent",
          summary["total_fixtures"] == 5, str(summary["total_fixtures"]))
    check("import_directory reports its own counts",
          summary["scanned"] == 1 and len(summary["imported"]) == 1
          and summary["errors"] == [],
          json.dumps({k: summary[k] for k in
                      ("scanned", "errors")}) + " "
          + str(len(summary["imported"])))

    # a corrupt GDTF must be visible, not a silent "0 imported"
    (tmp / "Broken-Brand-X.gdtf").write_bytes(b"PK\x03\x04 not a real zip")
    bad = import_gdtf.run(db, tmp)
    check("a corrupt file is reported as an error",
          len(bad["errors"]) == 1 and "Broken" in bad["errors"][0]["file"],
          json.dumps(bad["errors"]))
    check("the good file still imported alongside the bad one",
          bad["scanned"] == 2 and bad["total_fixtures"] == 5,
          json.dumps({k: bad[k] for k in
                      ("scanned", "total_fixtures")}))
    (tmp / "Broken-Brand-X.gdtf").unlink()


def test_showdesign() -> None:
    """show-from-a-prompt: the brief, the concepts, and loading one.

    `showdesign` is the console's own AI - it is what `#ai-design` calls
    through `/api/console/generate`, and it is why the AI survived the
    removal of the assistant page.  So this suite is now the thing that
    holds it up.
    """
    print("show design (console show-from-a-prompt)")
    from app import engine as eng_mod
    from app import showdesign

    # An empty brief must still produce something, because that is the case
    # the operator hits: they press the button before they have decided.
    thin = showdesign.design({})
    check("empty brief still designs",
          thin["ok"] and len(thin["concepts"]) == 3, json.dumps(thin)[:200])
    check("questions for every missing answer",
          len(thin["questions"]) == 4, str(thin["questions"]))
    check("assumptions listed", bool(thin["assumptions"]), "no assumptions")
    c0 = thin["concepts"][0]
    check("concept carries palette + cues",
          bool(c0["name"]) and bool(c0["palette"]) and 5 <= len(c0["cues"]) <= 7,
          json.dumps(c0)[:200])
    check("cue colours are hex",
          all(col.startswith("#") and len(col) == 7
              for cu in c0["cues"] for col in cu["colours"].values()),
          "non-hex colour in cues")
    check("cue fades and holds positive",
          all(cu["fade_s"] > 0 and cu["hold_s"] > 0 for cu in c0["cues"]),
          "bad timing")
    check("concepts differ",
          len({c["strategy"] for c in thin["concepts"]}) == 3
          and len({c["name"] for c in thin["concepts"]}) == 3,
          str([c["name"] for c in thin["concepts"]]))
    check("and it SAYS the stage is synthetic when no rig is patched, rather "
          "than quietly drawing on a rig that is not there",
          any("synthetic" in a for a in thin["assumptions"]),
          str(thin["assumptions"]))
    check("with no layout variants any more, the index is -1 and not a lie "
          "about a variant that does not exist",
          thin["variant_index"] == -1, str(thin.get("variant_index")))

    # brief parsing: colours, pace, structure, mood
    rich = showdesign.design({"mood": "techno club",
                              "colours": ["magenta", "#00ffcc"],
                              "pace": "fast", "structure": "box"})
    check("answered brief asks nothing", rich["questions"] == [],
          str(rich["questions"]))
    check("user colours parsed first",
          rich["brief"]["colours"][0] == "#ff4ddb"
          and "#00ffcc" in rich["brief"]["colours"],
          str(rich["brief"]["colours"]))
    check("structure forwarded + style hint",
          rich["brief"]["structure"] == "box" and "truss" in rich["style_hint"],
          rich["style_hint"])
    check("fast pace shortens the hits",
          all(cu["fade_s"] < 1 for cu in rich["concepts"][1]["cues"]),
          str([cu["fade_s"] for cu in rich["concepts"][1]["cues"]]))

    # THE STAGE IS THE RIG.  It used to be a layout session, which only the
    # rig studio could produce - so with the studio gone a real patched rig
    # would have been ignored in favour of a synthetic ten-head fake.  The
    # suite pins the new behaviour, because "faithful to the operator's own
    # positions" is exactly the kind of thing that quietly stops happening.
    saved = eng_mod.ENGINE
    try:
        eng_mod.ENGINE = eng_mod.Engine(dry_run=True)
        eng_mod.ENGINE.act("add_heads", query="LED PAR 4ch", qty=3, address=1)
        for h, y in zip(eng_mod.ENGINE.patch, (4.2, 4.2, 0.3)):
            h["y"] = y
        on_rig = showdesign.design({})
        stage = on_rig["concepts"][0]["stage"]
        check("with a rig patched, the stage IS that rig, at its own head "
              "numbers and positions",
              {f["head_no"] for f in stage["fixtures"]} == {1, 2, 3}
              and [round(f["y"], 1) for f in stage["fixtures"]] == [4.2, 4.2, 0.3],
              json.dumps(stage)[:220])
        check("and it says so, so the operator knows the preview is theirs",
              any("you have" in a for a in on_rig["assumptions"]),
              str(on_rig["assumptions"]))
        check("the stage box contains the rig rather than clipping it",
              stage["width"] > 0 and stage["depth"] > 0
              and all(-stage["width"] / 2 <= f["x"] <= stage["width"] / 2
                      for f in stage["fixtures"]),
              json.dumps({k: v for k, v in stage.items()
                          if k != "fixtures"})[:160])

        # ...and with NO rig, it falls back rather than drawing nothing.
        # `remove_heads`, not `clear_heads`: the latter drops PROGRAMMER
        # values for named heads and leaves the patch alone, so asking it
        # to empty a rig silently does nothing and the test passes for the
        # wrong reason.
        eng_mod.ENGINE.act("remove_heads",
                           heads=[h["head_no"] for h in eng_mod.ENGINE.patch])
        check("the rig really is empty before the fallback is tested",
              not eng_mod.ENGINE.patch,
              "%d head(s) left" % len(eng_mod.ENGINE.patch))
        empty = showdesign.design({})
        check("with an empty patch it falls back to a synthetic stage, and "
              "still says which it is",
              len(empty["concepts"][0]["stage"]["fixtures"]) >= 8
              and any("synthetic" in a for a in empty["assumptions"]),
              json.dumps(empty["assumptions"])[:160])

        # THE PATH THE CONSOLE ACTUALLY TAKES.  `#ai-load` posts a concept
        # to /api/console/import_show, which is the `import_show` action -
        # NOT the showbuild.program() the assistant page used.  So this is
        # the half of the old suite worth keeping, retargeted at the route
        # the operator's button actually uses.
        eng_mod.ENGINE.act("add_heads", query="LED PAR 4ch", qty=2, address=1)
        eng_mod.ENGINE.act("select_heads", heads=[1, 2])
        concept = showdesign.design({})["concepts"][0]
        r = eng_mod.ENGINE.act("import_show", concept=concept, playback=1)
        check("a concept loads onto a playback through `import_show`, which "
              "is what the console's own button posts",
              r.get("ok") and r.get("cues"), json.dumps(r)[:200])
        stack = eng_mod.ENGINE.playbacks[0]["stack"]
        n_cues = len(concept["cues"])
        check("and the cues really land on the stack",
              len(stack) == n_cues, "%d stack vs %d concept cues"
              % (len(stack), n_cues))
        check("with real levels, not a stack of zeros",
              all(row.get("dimmer") for row in stack[0]["values"].values()),
              json.dumps(stack[:1])[:200])
        check("and colours recorded as #rrggbb",
              all(str(col).startswith("#") and len(str(col)) == 7
                  for cu in concept["cues"] for col in cu["colours"].values()),
              "non-hex colour")
        bad = eng_mod.ENGINE.act("import_show",
                                 concept={"cues": []}, playback=1)
        check("an empty concept is refused by name, not silently ignored",
              not bad.get("ok") and "cues" in str(bad.get("error", "")),
              str(bad)[:160])
        eng_mod.ENGINE.shutdown()
    finally:
        eng_mod.ENGINE = saved


def test_artnet() -> None:
    print("Art-Net packets")
    from app import artnet

    pkt = artnet.build_artdmx(1, bytes(512), 1)
    check("packet size = 530", len(pkt) == 530, str(len(pkt)))
    check("header id Art-Net\\0", pkt[0:8] == b"Art-Net\0", repr(pkt[0:8]))
    check("opcode 0x5000 little-endian", pkt[8] == 0x00 and pkt[9] == 0x50,
          str(pkt[8:10]))
    check("protocol version 14", pkt[10] == 0x00 and pkt[11] == 0x0E,
          str(pkt[10:12]))
    check("length 512 big-endian", pkt[16] == 0x02 and pkt[17] == 0x00,
          str(pkt[16:18]))
    check("sequence byte", pkt[12] == 1, str(pkt[12]))
    check("physical byte = 0", pkt[13] == 0, str(pkt[13]))
    check("u1 -> SubUni 0, Net 0", pkt[14] == 0 and pkt[15] == 0,
          f"{pkt[14]},{pkt[15]}")

    p17 = artnet.build_artdmx(17, bytes(512), 1)
    check("u17 -> SubUni 16", p17[14] == 16, str(p17[14]))
    p256 = artnet.build_artdmx(256, bytes(512), 1)
    check("u256 -> SubUni 255, Net 0", p256[14] == 255 and p256[15] == 0,
          f"{p256[14]},{p256[15]}")
    p257 = artnet.build_artdmx(257, bytes(512), 1)
    check("u257 -> SubUni 0, Net 1", p257[14] == 0 and p257[15] == 1,
          f"{p257[14]},{p257[15]}")
    pnet = artnet.build_artdmx(1, bytes(512), 1, net=2)
    check("DMX_NET=2 shifts Net byte", pnet[15] == 2, str(pnet[15]))

    sender = artnet.ArtNetSender("127.0.0.1", 9, dry_run=True)
    seqs = [sender.next_sequence(1) for _ in range(300)]
    check("sequence cycles 1..255",
          seqs[:3] == [1, 2, 3] and seqs[254] == 255 and seqs[255] == 1,
          str(seqs[252:258]))
    check("sequence independent per universe", sender.next_sequence(2) == 1,
          str(sender.next_sequence(2)))

    data = bytes([1, 2, 3] + [0] * 509)
    dec = artnet.decode_artdmx(artnet.build_artdmx(5, data, 7))
    check("decode round trip",
          dec["data"] == data and dec["sequence"] == 7
          and dec["port_address"] == 4, str(dec["port_address"]))

    try:
        artnet.build_artdmx(1, bytes(513), 1)
        big = False
    except ValueError:
        big = True
    check("rejects > 512 slots", big)
    try:
        artnet.build_artdmx(0, bytes(512), 1)
        zero = False
    except ValueError:
        zero = True
    check("rejects universe < 1", zero)

    sent = sender.send(1, bytes(512))
    check("dry run puts nothing on the wire",
          sent is False and sender.frames_sent == 0
          and sender.simulated_frames == 1,
          str(sender.stats()))


def test_artnet_discovery() -> None:
    import socket as _socket
    import struct as _struct
    import threading as _threading
    import time as _time

    from app import artnet

    print("Art-Net discovery (ArtPoll / ArtPollReply / sniff)")

    poll = artnet.build_artpoll()
    check("poll size = 14", len(poll) == 14, str(len(poll)))
    check("poll id Art-Net\\0", poll[0:8] == b"Art-Net\0", repr(poll[0:8]))
    check("poll opcode 0x2000 little-endian", poll[8] == 0x00 and poll[9] == 0x20,
          str(poll[8:10]))
    check("poll ProtVer 14 big-endian", poll[10] == 0x00 and poll[11] == 0x0E,
          str(poll[10:12]))
    check("poll TalkToMe default = reply when polled", poll[12] == 0,
          str(poll[12]))
    check("poll Priority default = 0", poll[13] == 0, str(poll[13]))
    check("poll TalkToMe parameter",
          artnet.build_artpoll(talk_to_me=0x02)[12] == 0x02)
    check("parse rejects ArtPoll as reply",
          artnet.parse_artpollreply(poll) is None)
    check("parse rejects junk", artnet.parse_artpollreply(b"nope" * 4) is None)

    # Synthesize an ArtPollReply: node "NODE-1", port 1 out @291, port 2 in.
    pkt = bytearray(213)
    pkt[0:8] = b"Art-Net\0"
    _struct.pack_into("<H", pkt, 8, artnet.ARTPOLLREPLY_OP)
    pkt[10:14] = bytes((127, 0, 0, 1))            # IP (no ProtVer here!)
    _struct.pack_into("<H", pkt, 14, 6454)        # Port, little-endian
    _struct.pack_into(">H", pkt, 16, 0x0100)      # VersInfo, big-endian
    pkt[18] = 1                                   # NetSwitch
    pkt[19] = 2                                   # SubSwitch
    _struct.pack_into(">H", pkt, 20, 0x1234)      # Oem
    pkt[26:44] = b"NODE-1\0".ljust(18, b"\0")     # ShortName
    pkt[44:108] = b"Test Node\0".ljust(64, b"\0")  # LongName
    _struct.pack_into(">H", pkt, 172, 2)          # NumPorts
    pkt[174], pkt[175] = 0x80, 0x40               # PortTypes: out, in
    pkt[190], pkt[191] = 0x03, 0x09               # SwOut / SwIn
    pkt[200] = 0                                  # Style
    pkt[201:207] = bytes.fromhex("aabbccddeeff")  # MAC

    reply = artnet.parse_artpollreply(bytes(pkt))
    check("reply parsed", reply is not None)
    check("reply ip + port",
          reply and reply["ip"] == "127.0.0.1" and reply["port"] == 6454,
          str(reply))
    check("reply name", reply and reply.get("name") == "NODE-1",
          str(reply and reply.get("name")))
    check("reply version + oem",
          reply and reply.get("version") == 0x0100
          and reply.get("oem") == 0x1234, str(reply))
    check("reply mac",
          reply and reply.get("mac") == "aa:bb:cc:dd:ee:ff",
          str(reply and reply.get("mac")))
    # (1<<8)|(2<<4)|3 = 291; the input port (SwIn, type 0x40) must not count
    check("output ports only: 256+32+3 = 291",
          reply and reply.get("output_ports") == [291],
          str(reply and reply.get("output_ports")))
    cut = artnet.parse_artpollreply(bytes(pkt[:60]))
    check("truncated reply parses what it can",
          cut is not None and cut.get("name") == "NODE-1"
          and cut.get("output_ports") == [], str(cut))

    # Port-Address -> 1-based universe (inverse of the sender mapping).
    check("pa 0 -> universe 1", artnet.universe_from_port_address(0) == 1)
    check("pa 4 -> universe 5", artnet.universe_from_port_address(4) == 5)
    check("pa 291 -> universe 292", artnet.universe_from_port_address(291) == 292)
    check("net 1 inverse: pa 256 -> universe 1",
          artnet.universe_from_port_address(256, 1) == 1)
    check("net 1 inverse: pa 521 -> universe 266",
          artnet.universe_from_port_address(521, 1) == 266)
    check("below configured net falls back raw",
          artnet.universe_from_port_address(3, 1) == 4)

    # Live sniff: a helper "node" unicasts ArtPollReply + ArtDmx at us.
    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    def _fake_node() -> None:
        _time.sleep(0.3)                      # let scan() bind and poll
        node = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            node.sendto(bytes(pkt), ("127.0.0.1", port))       # announce
            used = bytearray(16)
            used[11] = 200                    # channel 12 is the last lit
            node.sendto(artnet.build_artdmx(1, bytes(used), 1),
                        ("127.0.0.1", port))   # universe 1, depth 12
            deep = bytearray(32)
            deep[31] = 255                    # channel 32 lit
            node.sendto(artnet.build_artdmx(292, bytes(deep), 1),
                        ("127.0.0.1", port))   # pa 291 -> announced + seen
        finally:
            node.close()

    worker = _threading.Thread(target=_fake_node, daemon=True)
    worker.start()
    found = artnet.scan(timeout=1.0, port=port, net=0)
    worker.join(timeout=2)
    if found["error"]:
        # Losing the bind race must degrade gracefully, never raise.
        check("scan bind failure degrades gracefully",
              found["universes"] == [] and found["nodes"] == []
              and found["polls_sent"] == 0, str(found))
        return
    check("scan polled at least twice", found["polls_sent"] >= 2,
          str(found["polls_sent"]))
    check("scan saw the node",
          found["replies"] >= 1 and found["nodes"]
          and found["nodes"][0]["name"] == "NODE-1"
          and found["nodes"][0]["output_ports"] == [291], str(found["nodes"]))
    rows = found["universes"]
    check("rows sorted by port address",
          [r["port_address"] for r in rows]
          == sorted(r["port_address"] for r in rows), str(rows))
    announced = [r for r in rows if r["port_address"] == 291]
    check("announced + sniffed universe: via both, u292, depth 32",
          announced and announced[0]["via"] == "both"
          and announced[0]["universe"] == 292
          and announced[0]["frames"] == 1
          and announced[0]["channels"] == 32
          and announced[0]["node"] == "NODE-1", str(announced))
    sniffed = [r for r in rows if r["port_address"] == 0]
    check("pure ArtDmx universe: via artdmx, u1, depth 12",
          sniffed and sniffed[0]["via"] == "artdmx"
          and sniffed[0]["universe"] == 1
          and sniffed[0]["channels"] == 12
          and sniffed[0]["node"] == "NODE-1", str(sniffed))


def test_channel_roles() -> None:
    print("channel roles")
    from app import engine as eng

    cases = {
        "Dimmer": "dimmer", "Red": "red", "Pan": "pan",
        "Tilt Fine": "tilt_fine", "Gobo 1 Rotate": "gobo_rot",
        "Zone 1 Dimmer": "zone_dimmer", "Zone 2 Red": "red",
        "Unused": "unused", "Pan/Tilt Speed": "speed",
        "Shutter": "shutter", "Weird Knob": "raw", "": "raw",
    }
    for label, want in cases.items():
        got = eng.channel_role(label)
        check(f"role {label!r} -> {want}", got == want, got)


def test_engine(tmp: Path) -> None:
    import time as _time

    from app import artnet
    from app import engine as eng

    print("console engine")
    db = tmp / "engine.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "shows")

    # --- patch: add / remove / address ---------------------------------
    r = e.act("add_heads", query="LED PAR 4ch", qty=3)
    check("add 3 heads", r["ok"] and r["heads"] == [1, 2, 3], str(r))
    addrs = [(h["universe"], h["address"], h["channels"]) for h in e.patch]
    check("auto addresses consecutive",
          addrs == [(1, 1, 4), (1, 5, 4), (1, 9, 4)], str(addrs))
    check("channel map resolved",
          e.patch[0]["map"] == ["dimmer", "red", "green", "blue"]
          and e.patch[0]["mapped"], str(e.patch[0]["map"]))

    e.act("select_all")
    r = e.act("group_create", name="Front")
    check("group from selection", r["ok"] and r["heads"] == [1, 2, 3],
          str(r))
    r = e.act("remove_heads", heads=[2])
    check("remove keeps other head numbers",
          r["ok"] and [h["head_no"] for h in e.patch] == [1, 3], str(r))
    check("groups pruned after remove",
          e.groups and e.groups[0]["heads"] == [1, 3], str(e.groups))

    r = e.act("set_address", head=3, universe=1, address=2)
    check("overlap rejected by name",
          not r["ok"] and "head 1" in str(r["error"]), str(r["error"]))
    r = e.act("set_address", head=3, universe=2, address=10)
    check("free address accepted", r["ok"] and r["universe"] == 2, str(r))

    # --- merge: HTP / LTP / master / blackout ---------------------------
    e.act("select_all")
    e.act("set_intensity", level=70)
    b = e.build_frames()[1]
    check("HTP dimmer 70% -> DMX 178", b[0] == 178, str(list(b[:8])))
    b2 = e.build_frames()[2]
    check("moved head follows its address", b2[_pos_byte(2, 10)] == 178,
          str(list(b2[8:14])))
    e.act("set_colour", hex="#ff0000")
    b = e.build_frames()[1]
    check("colour red written", b[1] == 255 and b[2] == 0 and b[3] == 0,
          str(list(b[:8])))

    e.act("blackout", state=1)
    b = e.build_frames()[1]
    check("blackout masks intensity", b[0] == 0, str(b[0]))
    check("blackout leaves colour intact", b[1] == 255, str(b[1]))
    e.act("blackout", state=0)
    e.act("master", level=50)
    b = e.build_frames()[1]
    check("grand master scales intensity", b[0] == 178 * 50 // 100,
          str(b[0]))
    e.act("master", level=100)

    # --- cues, playbacks, LTP priority ---------------------------------
    r = e.act("record_cue", playback=1, name="Red 70", fade=0)
    check("record cue clears programmer",
          r["ok"] and not e.programmer, str(r))
    r = e.act("cue_go", playback=1)
    check("GO activates playback", r["ok"] and r["cue"] == 1, str(r))
    b = e.build_frames()[1]
    check("cue drives the frame", b[0] == 178 and b[1] == 255,
          str(list(b[:4])))
    e.act("select_all")
    e.act("set_colour", hex="#0000ff")
    b = e.build_frames()[1]
    check("programmer LTP beats playback",
          b[1] == 0 and b[3] == 255, str(list(b[:4])))
    check("playback still owns dimmer (HTP)", b[0] == 178, str(b[0]))

    # --- fades ----------------------------------------------------------
    pb = eng._new_playback(7)
    pb["stack"] = [
        {"n": 1, "name": "up", "fade_s": 2.0, "hold_s": 0.0,
         "values": {1: {"dimmer": 100}}},
        {"n": 2, "name": "down", "fade_s": 2.0, "hold_s": 0.0,
         "values": {1: {"dimmer": 0}}},
    ]
    pb["index"] = 0
    now = _time.monotonic()
    pb["fade"] = {"t0": now, "dur": 2.0, "from": {1: {"dimmer": 0}}}
    check("fade t=0 = from", e._pb_values(pb, now)[1]["dimmer"] == 0,
          str(e._pb_values(pb, now)))
    check("fade midpoint", e._pb_values(pb, now + 1.0)[1]["dimmer"] == 50,
          str(e._pb_values(pb, now + 1.0)))
    check("fade complete = cue",
          e._pb_values(pb, now + 9.0)[1]["dimmer"] == 100,
          str(e._pb_values(pb, now + 9.0)))

    # --- auto patch scale ------------------------------------------------
    e3 = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "shows3")
    r = e3.act("add_heads", query="Moving Head", qty=40)
    check("add 40 moving heads", r["ok"] and len(r["heads"]) == 40,
          str(r)[:140])
    e3.act("auto_patch")
    check("auto patch fits 2 universes", e3._universe_count() == 2,
          str(e3._universe_count()))
    u1 = [h for h in e3.patch if h["universe"] == 1]
    check("u1 packed to exactly 512",
          sum(h["channels"] for h in u1) == 512,
          str(sum(h["channels"] for h in u1)))
    try:
        e3._validate_patch()
        clean = True
    except ValueError:
        clean = False
    check("no overlaps after auto patch", clean)

    # --- csv -------------------------------------------------------------
    csv_text = ("Headno,Headname,Dmxno,Manufacturer,Type,Chans,X,Y,Z\n"
                "0001,Side L,1-001,Generic,LED PAR 4ch,4,0,0,0\n")
    r = e.act("patch_from_csv", csv=csv_text)
    check("csv import", r["ok"] and r["heads"] == 1, str(r))
    check("csv map resolved from db",
          e.patch[0]["map"] == ["dimmer", "red", "green", "blue"]
          and e.patch[0]["mapped"], str(e.patch[0]["map"]))
    r = e.act("patch_from_csv", csv="nope,header\n1,x")
    check("csv missing columns reported",
          not r["ok"] and "column" in str(r["error"]), str(r["error"]))

    # --- show files --------------------------------------------------------
    e.act("patch_from_csv", csv=csv_text)
    e.act("select_all")
    e.act("set_intensity", level=60)
    e.act("record_cue", playback=1, name="Base", fade=1)
    r = e.act("save_show", name="engine-test")
    check("save show",
          r["ok"] and (tmp / "shows" / "engine-test.json").is_file(), str(r))
    r = e.act("save_show", name="../evil")
    check("path traversal rejected", not r["ok"], str(r["error"]))
    saved_cues = len(e.playbacks[0]["stack"])
    e.act("patch_clear")
    check("patch clear", not e.patch)
    r = e.act("load_show", name="engine-test")
    check("load restores patch",
          r["ok"] and len(e.patch) == 1 and e.patch[0]["head_no"] == 1,
          str(r))
    check("load restores cue stack",
          len(e.playbacks[0]["stack"]) == saved_cues
          and e.playbacks[0]["stack"], str(e.playbacks[0]["stack"]))

    # --- live gate ---------------------------------------------------------
    check("engine is the console (single mode)",
          e.mode == "jarvis" and not hasattr(e, "set_mode"), e.mode)
    safe = artnet.ArtNetSender("127.0.0.1", 9, dry_run=True)
    e2 = eng.Engine(db_path=db, dry_run=False, sender=safe,
                    show_dir=tmp / "shows2")
    r = e2.act("set_output", state=True)
    check("armed output needs confirm",
          not r["ok"] and "confirm" in str(r["error"]), str(r["error"]))
    r = e2.act("set_output", state=True, confirm=True)
    check("confirm starts output", r["ok"] and e2.live, str(r))
    _time.sleep(0.08)
    check("output thread running", e2.output["running"],
          str(e2.output))
    e2.act("set_output", state=False)
    _time.sleep(0.05)
    check("stop halts output thread", not e2.output["running"],
          str(e2.output))
    e2.shutdown()

    # --- lite feed ----------------------------------------------------------
    check("lite omits heads when rev matches",
          "heads" not in e.lite(rev=e.patch_rev), str(
              sorted(e.lite(rev=e.patch_rev))))
    lite = e.lite(rev=-1)
    check("lite sends heads on rev change",
          len(lite.get("heads") or []) == len(e.patch), str(
              len(lite.get("heads") or [])))
    check("lite carries output stats",
          "frames_sent" in lite["output"] and "drift_ms" in lite["output"],
          str(sorted(lite["output"])))

    # --- concept import ------------------------------------------------------
    concept = {"name": "Test show", "cues": [
        {"n": 1, "name": "Open", "fade_s": 2.0, "hold_s": 1.0,
         "active": ["generic", "wash"],
         "colours": {"generic": "#ff8800", "wash": "#ff8800"},
         "intensity": {"generic": 80, "wash": 80}},
        {"n": 2, "name": "Hit", "fade_s": 0.1, "hold_s": 0.5,
         "active": ["generic", "wash"],
         "colours": {"generic": "#ffffff", "wash": "#ffffff"},
         "intensity": {"generic": 100, "wash": 100}},
    ]}
    r = e.act("import_show", concept=concept, playback=3)
    check("concept imports into a stack",
          r["ok"] and r["cues"] == 2, str(r))
    first = e.playbacks[2]["stack"][0]["values"]
    check("cue holds dimmer 80",
          any(row.get("dimmer") == 80 for row in first.values()),
          str(first))
    check("cue holds concept colour",
          any(row.get("red") == 255 and row.get("green") == 136
              for row in first.values()), str(first))
    r = e.act("cue_go", playback=3)
    check("imported stack plays", r["ok"] and r["cue"] == 1, str(r))

    # --- concept roles the patch does not use (playback "does nothing") ---
    # A design concept names roles (wash/beam/...); a CSV-imported patch is
    # all "generic".  Applying the roles literally blacked out every cue,
    # so playback looked broken.  It must fall back to "light everything".
    mism = {"name": "Roles", "active": ["wash", "beam"],
            "colours": {"wash": "#ff0000", "beam": "#0000ff"},
            "intensity": {"wash": 90, "beam": 60},
            "cues": [{"n": 1, "name": "Open", "fade_s": 0.0, "hold_s": 0.0,
                      "active": ["wash", "beam"]}]}
    r = e.act("import_show", concept=mism, playback=4)
    row = e.playbacks[3]["stack"][0]["values"].get(1, {})
    check("unknown concept roles fall back to all heads",
          r["ok"] and r.get("role_fallback") is True and row.get("dimmer") == 90,
          f"{r.get('summary')} {row}")
    e.act("cue_go", playback=4)
    check("fallback cue puts light on the wire",
          e.build_frames()[1][0] > 0, str(e.build_frames()[1][0]))

    # --- cue stack survives a JSON round-trip (head keys must be ints) ---
    for num in (1, 3, 4):                    # earlier tests left these live
        e.act("playback_release", playback=num)
    e.act("patch_clear")
    e.act("add_heads", query="LED PAR 4ch", qty=1, universe=1, address=1)
    e.act("select_all")
    e.act("set_intensity", level=60)
    e.act("record_cue", playback=5, name="Round trip", fade=0)
    e.act("save_show", name="roundtrip")
    e.act("patch_clear")
    e.act("load_show", name="roundtrip")
    stack5 = e.playbacks[4]["stack"]
    check("loaded cue keys are ints, not strings",
          bool(stack5) and all(isinstance(k, int) for k in stack5[0]["values"]),
          str(stack5[:1]))
    e.act("clear_programmer")
    e.act("cue_go", playback=5)
    check("cue loaded from json actually drives the fixture",
          e.build_frames()[1][0] == int(0.6 * 255), str(e.build_frames()[1][0]))

    # --- GO at the end of a stack is honest, not a silent re-run ----------
    r = e.act("cue_go", playback=5)
    check("GO on the last cue reports the end",
          r["ok"] and r.get("ended") and "last cue" in str(r.get("summary")),
          str(r))
    e.act("follow_set", playback=5, loop=True)
    r = e.act("cue_go", playback=5)
    check("GO on the last cue wraps when looping",
          r["ok"] and not r.get("ended") and r["cue"] == 1, str(r))
    e.act("follow_set", playback=5, loop=False)
    e.act("playback_release", playback=5)
    r = e.act("cue_go", playback=5)
    check("GO on a released stack starts it over",
          r["ok"] and not r.get("ended") and r["cue"] == 1, str(r))

    e.shutdown()


def test_autopatch(tmp: Path) -> None:
    """Auto-patching: profile library, dynamic addressing, rollover."""
    print("auto patch engine")
    from app import engine as eng

    db = tmp / "autopatch.db"
    fixtures.seed_generics(db)

    def new_engine(tag: str):
        return eng.Engine(db_path=db, dry_run=True, show_dir=tmp / f"shows-{tag}")

    # --- footprint rollover at the 512-channel boundary ----------------
    e = new_engine("ap1")
    r = e.act("add_heads", query="RGBW Bar 12ch", qty=43)
    addrs = [(h["universe"], h["address"], h["channels"]) for h in e.patch]
    check("43rd head rolls to universe 2",
          r["ok"] and addrs[-1] == (2, 1, 12)
          and max(a - 1 + c for u, a, c in addrs if u == 1) <= 512,
          str(addrs[-2:]))
    check("no head straddles after auto addresses",
          all(a - 1 + c <= 512 for u, a, c in addrs), str(addrs[:3]))

    # --- plan_addresses: list of desired fixtures -> start addresses ---
    e = new_engine("ap2")
    plan = e.plan_addresses([{"query": "LED PAR 4ch", "qty": 2},
                             {"footprint": 510},
                             {"footprint": 4}])
    got = [(row["universe"], row["address"], row["channels"]) for row in plan]
    check("plan is read-only",
          not e.patch and len(plan) == 4, f"patch={len(e.patch)} plan={got}")
    check("plan rolls over the boundary",
          got == [(1, 1, 4), (1, 5, 4), (2, 1, 510), (3, 1, 4)], str(got))
    e.act("add_heads", query="LED PAR 4ch", qty=1)
    got = [(row["universe"], row["address"]) for row in
           e.plan_addresses([{"footprint": 4}], start=(1, 1))]
    check("plan skips occupied addresses", got == [(1, 5)], str(got))

    # --- patch_list: dry run first, then apply -------------------------
    r = e.act("patch_list",
              fixtures=[{"query": "Moving Head", "qty": 2}], plan_only=True)
    check("patch_list plan_only patches nothing",
          r["ok"] and r["planned"] == 2 and len(e.patch) == 1, str(r))
    r = e.act("patch_list", fixtures=[{"query": "Moving Head", "qty": 2}])
    check("patch_list applies the plan",
          r["ok"] and r["heads"] == [2, 3]
          and [(h["universe"], h["address"]) for h in e.patch[1:]]
          == [(1, 5), (1, 21)], str(r))
    r = e.act("patch_list", fixtures="not a list")
    check("patch_list rejects a non-list",
          not r["ok"] and "list" in str(r["error"]), str(r["error"]))

    # --- auto_patch repairs a straddling (legacy) patch ----------------
    e = new_engine("ap3")
    e.act("add_heads", query="RGBW Bar 12ch", qty=43)
    e.patch[42]["universe"], e.patch[42]["address"] = 1, 505  # legacy split
    try:
        e._validate_patch()
        bad = ""
    except ValueError as exc:
        bad = str(exc)
    check("straddling patch rejected", "straddles" in bad, bad)
    r = e.act("auto_patch")
    addrs = [(h["universe"], h["address"], h["channels"]) for h in e.patch]
    check("auto patch rolls instead of straddling",
          r["ok"] and addrs[-1] == (2, 1, 12)
          and all(a - 1 + c <= 512 for u, a, c in addrs), str(addrs[-2:]))

    # --- import_scan: observed universes auto-patched ------------------
    e = new_engine("ap4")
    r = e.act("import_scan", observed=[
        {"universe": 1, "channels": 12},
        {"universe": 2, "channels": 60},
        {"universe": 1, "channels": 9}])          # duplicate row ignored
    u1 = [h for h in e.patch if h["universe"] == 1]
    u2 = [h for h in e.patch if h["universe"] == 2]
    check("scan patches each observed universe",
          r["ok"] and len(u1) == 3 and len(u2) == 15
          and r["universes"] == [1, 2] and r["skipped"] == [],
          f"u1={len(u1)} u2={len(u2)} {r}")
    check("scan addresses from the universe top",
          [h["address"] for h in u1] == [1, 5, 9]
          and u2[0]["address"] == 1, str([h["address"] for h in u1]))
    r = e.act("import_scan", observed=[{"universe": 1, "channels": 12},
                                       {"universe": 2, "channels": 60}])
    check("rescan never disturbs a patched universe",
          not r["ok"] and "already patched" in str(r["error"]),
          str(r["error"]))
    r = e.act("import_scan", observed=[{"universe": 4, "channels": 5000}])
    u4 = [h for h in e.patch if h["universe"] == 4]
    check("scan quantity clamped to 64", r["ok"] and len(u4) == 64,
          str(len(u4)))

    # --- profile library: offline fallback + typo guard ----------------
    e = new_engine("ap5")
    r = e.act("add_heads", query="strobe unit", qty=1)
    head = e.patch[0] if e.patch else {}
    check("fallback footprint library answers",
          r["ok"] and head.get("channels") == 2 and head.get("mapped")
          and head.get("unverified"), str(r))
    r = e.act("add_heads", query="zzz qqq", qty=1)
    check("unknown type still fails loudly",
          not r["ok"] and "no fixture matches" in str(r["error"]),
          str(r["error"]))

    # --- pinned addresses stay strict ----------------------------------
    r = e.act("add_heads", query="LED PAR 4ch", qty=1,
              universe=1, address=510)
    check("pinned address refuses to straddle",
          not r["ok"] and "try universe 2" in str(r["error"]),
          str(r["error"]))
    r = e.act("add_heads", query="LED PAR 4ch", qty=1,
              universe=1, address=1)
    check("pinned address refuses an overlap",
          not r["ok"] and "overlaps" in str(r["error"]), str(r["error"]))


def test_fx_autosave(tmp: Path) -> None:
    """FX wave engine (resolve, precedence, expiry) and autosave/restore."""
    print("fx + autosave")
    from app import engine as eng
    from app import fx as fxmod

    db = tmp / "fx.db"
    fixtures.seed_generics(db)

    # --- wave maths (shared by the DMX wire and the visualiser) --------
    check("sine wave endpoints",
          fxmod.wave("sine", 0.0) == 0.0
          and abs(fxmod.wave("sine", 0.5) - 1.0) < 1e-9
          and abs(fxmod.wave("sine", 0.25) - 0.5) < 1e-9,
          str([fxmod.wave("sine", c) for c in (0.0, 0.25, 0.5)]))
    check("saw ramps within a cycle",
          fxmod.wave("saw", 1.75) == 0.75 and fxmod.wave("saw", 2.0) == 0.0,
          str([fxmod.wave("saw", c) for c in (1.75, 2.0)]))
    check("square and triangle step",
          fxmod.wave("square", 0.25) == 1.0
          and fxmod.wave("square", 0.75) == 0.0
          and fxmod.wave("triangle", 0.5) == 1.0,
          str([fxmod.wave("square", 0.75), fxmod.wave("triangle", 0.5)]))
    check("random wave deterministic per cycle",
          fxmod.wave("random", 3.5, seed=7)
          == fxmod.wave("random", 3.5, seed=7)
          and 0.0 <= fxmod.wave("random", 3.5, seed=7) <= 1.0,
          str(fxmod.wave("random", 3.5, seed=7)))
    try:
        fxmod.wave("wobble", 0.0)
        bad_wave = ""
    except ValueError as exc:
        bad_wave = str(exc)
    check("unknown wave refused by maths", "unknown wave" in bad_wave,
          bad_wave)

    # --- engine FX: resolve, spread, precedence, DMX, expiry -----------
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "shows-fx")
    e.act("add_heads", query="LED PAR 4ch", qty=3)
    e.act("select_all")
    e.act("set_intensity", level=80)             # programmer HTP baseline
    r = e.act("run_fx", attribute="dimmer", wave="sine", speed=2.0,
              spread=180, duration=60)
    check("run_fx starts on the selection",
          r["ok"] and r["heads"] == 3 and r["fx"] >= 1, str(r))
    row = e.fx[0]
    vals = e._fx_values(now=row["t0"] + 0.125)   # +0.25 turns at 2 Hz
    levels = [v["dimmer"] for v in vals.values() if "dimmer" in v]
    check("fx resolves for every head", len(vals) == 3 and len(levels) == 3,
          str(vals))
    check("fx spread staggers the wave",
          max(levels) - min(levels) > 10, str(levels))
    check("fx replaces the programmer at merge time",
          80 not in levels, str(levels))
    frame = e.build_frames()[1]
    dmxx = [frame[_pos_byte(1, 1 + 4 * i)] for i in range(3)]
    check("fx reaches the DMX frame",
          max(dmxx) - min(dmxx) > 20, str(dmxx))

    # speed + spread clamps keep a runaway value on the road
    second = e.act("run_fx", attribute="red", wave="saw",
                   speed=999, spread=5000)
    check("fx clamps speed and spread",
          second["ok"] and e.fx[-1]["speed"] == fxmod.SPEED_MAX
          and e.fx[-1]["spread"] == fxmod.SPREAD_MAX, str(e.fx[-1]))

    e._fx_values(now=row["t0"] + 61.0)           # first effect ages out
    check("fx expires by duration",
          len(e.fx) == 1 and e.fx[0]["id"] == second["fx"], str(e.fx))

    # --- the UI feed (_fx_public) is a DIFFERENT code path from the wire
    # (_fx_values) and used to be duplicated in this class: a dead copy
    # with a different shape sat above the live one, so the UI countdown
    # would have silently vanished if the live copy had ever been removed.
    # Pin the shape the client actually renders, and the pruning.
    e.act("run_fx", attribute="dimmer", wave="sine", speed=2,
          spread=30, duration=30)
    public = e._fx_public()
    check("the fx feed reports the running effect",
          len(public) >= 1, str(public)[:200])
    row_pub = next((r for r in public if r["id"] == e.fx[-1]["id"]), None)
    check("the fx feed row exists for a live effect",
          row_pub is not None, str([r["id"] for r in public]))
    # every field console.js renderFx() reads must be present, or the
    # list silently renders blank/partial rows
    for field in ("id", "kind", "role", "speed", "spread", "heads",
                  "remaining"):
        check(f"the fx feed carries {field!r}",
              row_pub is not None and field in row_pub,
              str(sorted(row_pub)) if row_pub else "no row")
    check("the fx feed never leaks the monotonic clock",
          row_pub is not None and "t0" not in row_pub,
          str(sorted(row_pub)) if row_pub else "no row")
    check("the fx feed counts the time down",
          row_pub is not None and 0 < row_pub["remaining"] <= 30,
          str(row_pub.get("remaining")) if row_pub else "no row")
    # an effect that has already expired must not be advertised to the UI.
    # Capture the id FIRST: _fx_public() prunes self.fx as a side effect, so
    # reading e.fx[-1] after calling it would compare against the wrong row.
    expired_id = e.fx[-1]["id"]
    e.fx[-1]["t0"] -= 999
    after_prune = e._fx_public()
    check("the fx feed drops an expired effect",
          not any(r["id"] == expired_id for r in after_prune),
          str([r["id"] for r in after_prune]))
    check("pruning actually removed it from the engine too",
          not any(r["id"] == expired_id for r in e.fx),
          str([r["id"] for r in e.fx]))
    check("the lite feed agrees with the fx feed",
          e.lite()["fx"] == e._fx_public(), str(e.lite()["fx"])[:160])
    check("the snapshot agrees with the fx feed",
          e.snapshot()["fx"] == e._fx_public(), str(e.snapshot()["fx"])[:160])

    r = e.act("run_fx", attribute="tilt", kind="square", speed=3)
    check("kind is an alias for wave", r["ok"], str(r))
    r = e.act("run_fx", attribute="dimmer", wave="wobble")
    check("unknown wave refused", not r["ok"], str(r["error"]))
    r = e.act("stop_fx", id=e.fx[0]["id"])
    check("stop one fx", r["ok"] and r["stopped"] == 1
          and len(e.fx) == 1, str(r))
    e.act("run_fx", attribute="zoom")
    r = e.act("clear_programmer")
    check("clear stops every fx",
          r["ok"] and r["fx"] == 2 and not e.fx, str(r))
    e.act("run_fx", attribute="pan")
    r = e.act("stop_fx")
    check("stop with no id stops all",
          r["ok"] and r["stopped"] == 1 and not e.fx, str(r))

    # --- autosave: progress survives a crash ---------------------------
    save_path = tmp / "autosave.json"
    a = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "shows-as", autosave_path=save_path)
    a.act("add_heads", query="LED PAR 4ch", qty=2)
    a.act("select_all")
    a.act("set_intensity", level=55)
    a._autosave(force=True)                      # final state, past throttle
    check("autosave wrote progress", save_path.is_file(), str(save_path))
    b = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "shows-rs", autosave_path=save_path,
                   restore=True)
    check("restore brings the patch back",
          len(b.patch) == 2 and b.patch[0]["channels"] == 4
          and [h["address"] for h in b.patch] == [1, 5], str(len(b.patch)))
    check("restore brings programmer + selection back",
          b.programmer.get(1, {}).get("dimmer") == 55
          and b.selected == [1, 2],
          f"{b.programmer} {b.selected}")
    # shut the first engine down BEFORE corrupting the file: its autosave
    # writer thread would otherwise flush a good file over ours
    b.shutdown()
    save_path.write_text("{ not json", encoding="utf-8")
    c = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "shows-bad", autosave_path=save_path,
                   restore=True)
    check("corrupt autosave ignored", not c.patch, str(len(c.patch)))
    c.shutdown()

    # the writer thread must land the queued state on disk, and a forced
    # save at shutdown must not lose the last edit
    d = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "shows-w",
                   autosave_path=tmp / "w.json")
    d.act("add_heads", query="LED PAR 4ch", qty=3)
    d._autosave(force=True)
    saved_ok = (tmp / "w.json").is_file()
    if saved_ok:
        payload = json.loads((tmp / "w.json").read_text(encoding="utf-8"))
        saved_ok = len(payload.get("patch") or []) == 3
    check("autosave writer puts the patch on disk", saved_ok, "")
    d.shutdown()
    again = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "shows-w2",
                       autosave_path=tmp / "w.json", restore=True)
    check("written autosave restores on the next boot",
          len(again.patch) == 3, str(len(again.patch)))
    again.shutdown()


def _pos_byte(universe: int, address: int) -> int:
    """Flat byte index inside one universe's frame (address is 1-based)."""
    return address - 1


def test_console_ai(tmp: Path) -> None:
    print("AI console compiler")
    from app import console_ai
    from app import engine as eng_mod

    # The suite must never depend on a live model: force the offline path
    # (llm.available() reads config.LLM_API_KEY at call time).
    saved_key = config.LLM_API_KEY
    config.LLM_API_KEY = ""
    try:
        out = console_ai.plan("blackout now", offline=True)
        check("fallback compiles blackout",
              out["source"] == "fallback"
              and len(out["steps"]) == 1
              and out["steps"][0]["action"] == "blackout"
              and out["steps"][0]["attributes"].get("state") == 1,
              json.dumps(out))
        check("fallback reply is a sentence",
              isinstance(out["reply"], str) and out["reply"],
              str(out.get("reply")))

        out = console_ai.plan("everything to 70%", offline=True)
        check("intensity compiles with target all",
              [s["action"] for s in out["steps"]] == ["set_intensity"]
              and out["steps"][0]["target"] == "all"
              and out["steps"][0]["attributes"]["level"] == 70,
              json.dumps(out))

        rainbow = console_ai.plan("rainbow across the rig", offline=True)
        fx = [s for s in rainbow["steps"] if s["action"] == "run_fx"]
        check("rainbow is the library's Rainbow effect, spread across the rig",
              len(fx) == 1 and fx[0]["fx"]["name"] == "rainbow"
              and fx[0]["fx"]["params"]["spread"] == 180, json.dumps(rainbow))

        out = console_ai.plan("add 4 pars", offline=True)
        check("add pars -> add_heads qty 4",
              out["steps"]
              and out["steps"][0]["action"] == "add_heads"
              and out["steps"][0]["attributes"]["qty"] == 4
              and "PAR" in out["steps"][0]["attributes"]["query"],
              json.dumps(out))

        out = console_ai.plan("make it sparkle for 10 seconds", offline=True)
        fx = [s for s in out["steps"] if s["action"] == "run_fx"]
        check("duration lands in fx timing",
              fx and fx[0]["fx"].get("duration") == 10.0
              and fx[0]["fx"].get("name") == "sparks", json.dumps(out))

        # --- allowlist ---------------------------------------------------
        for bad in sorted(console_ai.DENY_ACTIONS):
            try:
                console_ai._validate({"reply": "x", "steps": [
                    {"target": "auto", "action": bad}]})
                denied = False
                detail = ""
            except ValueError as exc:
                denied = "not available to the AI" in str(exc)
                detail = str(exc)
            check(f"deny {bad}", denied, detail)
        try:
            console_ai._validate({"steps": [{"action": "be_normal"}]})
            unknown, detail = False, ""
        except ValueError as exc:
            unknown = "unknown action" in str(exc)
            detail = str(exc)
        check("unknown action refused", unknown, detail)
        try:
            console_ai._validate({"steps": [
                {"action": "set_intensity", "attributes": {"volume": 3}}]})
            foreign, detail = False, ""
        except ValueError as exc:
            foreign = "not a parameter" in str(exc)
            detail = str(exc)
        check("foreign parameter refused", foreign, detail)
        try:
            console_ai._validate({"steps": [
                {"action": "blackout", "fx": {"speed": 2}}]})
            fxoff, detail = False, ""
        except ValueError as exc:
            fxoff = "run_fx" in str(exc)
            detail = str(exc)
        check("fx refused off run_fx", fxoff, detail)
        check("system prompt documents the actions",
              "run_fx" in console_ai.SYSTEM
              and "set_intensity" in console_ai.SYSTEM,
              str(len(console_ai.SYSTEM)))

        # --- resolve -----------------------------------------------------
        simple = console_ai.plan("everything to 40%", offline=True)
        calls = console_ai.resolve(simple["steps"])
        check("resolve inserts select_all before intensity",
              [c["action"] for c in calls]
              == ["select_all", "set_intensity"], json.dumps(calls))

        dedup = console_ai.resolve(console_ai._validate({"steps": [
            {"target": "all", "action": "set_intensity", "attributes": {"level": 50}},
            {"target": "all", "action": "set_colour", "attributes": {"hex": "#ff0000"}},
            {"target": "all", "action": "run_fx", "fx": {"name": "breathe"}}]})["steps"])
        check("select_all deduped across steps",
              [c["action"] for c in dedup]
              == ["select_all", "set_intensity", "set_colour", "run_fx"],
              json.dumps(dedup))

        group = console_ai.resolve(
            console_ai.plan("group 2 pulse", offline=True)["steps"])
        check("group target feeds run_fx natively",
              len(group) == 1
              and group[0]["params"].get("group") == 2, json.dumps(group))

        fader = console_ai.resolve(
            console_ai.plan("playback 3 to 60%", offline=True)["steps"])
        check("playback target injects the playback kwarg",
              any(c["action"] == "playback_level"
                  and c["params"].get("playback") == 3
                  and c["params"].get("level") == 60 for c in fader),
              json.dumps(fader))

        # --- run against a real engine ----------------------------------
        db = tmp / "ai.db"
        fixtures.seed_generics(db)
        e = eng_mod.Engine(db_path=db, dry_run=True, show_dir=tmp / "ai-shows")
        res = console_ai.run(
            console_ai.resolve(console_ai.plan("add 4 pars",
                                               offline=True)["steps"], e), e)
        check("apply: patch 4 pars",
              res["ok"] and res["executed"] == 1 and len(e.patch) == 4,
              json.dumps(res))

        res = console_ai.run(
            console_ai.resolve(console_ai.plan("everything to 70%",
                                               offline=True)["steps"], e), e)
        check("apply: auto target selects, then sets level",
              res["ok"] and res["executed"] == 2 and e.selected == [1, 2, 3, 4]
              and e.programmer.get(1, {}).get("dimmer") == 70,
              json.dumps(res))

        res = console_ai.run(
            console_ai.resolve(console_ai.plan("pulse", offline=True)["steps"],
                               e), e)
        check("apply: fx runs through auto target",
              res["ok"] and len(e.fx) == 1, json.dumps(res))

        res = console_ai.run(
            console_ai.resolve(console_ai.plan("blackout", offline=True)
                               ["steps"], e), e)
        check("apply: blackout flips the engine",
              res["ok"] and e.blackout, json.dumps(res))

        e.act("clear_selection")
        res = console_ai.run([{"step": 1, "action": "set_intensity",
                               "params": {"level": 50}}], e)
        check("failed step stops the script",
              res["ok"] is False and res["executed"] == 0
              and res.get("error") and len(res["steps_run"]) == 1,
              json.dumps(res))

        # --- LLM path (mocked): fenced JSON + garbage fallback ----------
        original_chat = console_ai.llm.chat
        try:
            config.LLM_API_KEY = "selftest-key"
            seen = {}

            def fenced(messages, tools=None, tool_choice=None, temperature=0.3):
                seen["messages"] = messages
                seen["tools"] = tools
                return {"content": '```json\n{"reply":"dimmed","steps":[{"target":'
                                   '"all","action":"set_intensity","attributes":'
                                   '{"level":40}}]}\n```'}
            console_ai.llm.chat = fenced
            out = console_ai.plan("to 40", eng=e, history=[
                {"role": "user", "content": "warm wash"},
                {"role": "assistant", "content": "Warm wash on everything."}])
            check("LLM path parses fenced JSON",
                  out.get("source") == "llm"
                  and out["steps"][0]["attributes"]["level"] == 40,
                  json.dumps(out))
            check("the model is asked through a schema (a forced tool call)",
                  seen.get("tools") and seen["tools"][0]["function"]["name"] == "plan",
                  str(seen.get("tools"))[:120])
            sysmsg = seen["messages"][0]["content"]
            check("and it is shown the actual rig: heads, type and capabilities",
                  "RIG: 4 fixtures" in sysmsg and "heads 1-4" in sysmsg
                  and "can do:" in sysmsg, sysmsg[-400:])
            check("and the effects library by name",
                  "NAMED EFFECTS" in sysmsg and "circle" in sysmsg, "")
            check("follow-ups carry the conversation",
                  [m["role"] for m in seen["messages"]] ==
                  ["system", "user", "assistant", "user"], "")
            console_ai.llm.chat = lambda messages, tools=None, tool_choice=None, temperature=0.3: {
                "tool_calls": [{"function": {"name": "plan", "arguments": json.dumps(
                    {"reply": "circle", "answer": "You have 4 PARs.",
                     "steps": [{"target": "heads 1,3", "action": "run_fx",
                                "fx": {"name": "Rainbow"}}]})}}]}
            out = console_ai.plan("rainbow on 1 and 3", eng=e)
            check("a tool-call answer is read, and a named effect is normalised",
                  out.get("source") == "llm" and out["steps"][0]["fx"]["name"] == "rainbow"
                  and out.get("answer") == "You have 4 PARs.", json.dumps(out))
            calls = console_ai.resolve(out["steps"], e)
            check("scattered heads go straight to the effect",
                  calls[-1]["params"].get("heads") == [1, 3], json.dumps(calls))
            console_ai.llm.chat = lambda messages, tools=None, tool_choice=None, temperature=0.3: {
                "tool_calls": [{"function": {"name": "plan", "arguments": json.dumps(
                    {"reply": "x", "steps": [{"action": "set_output",
                                              "attributes": {"state": 1}}]})}}]}
            out = console_ai.plan("go live", eng=e)
            check("a model that tries to arm the output is refused, and the "
                  "offline compiler answers instead",
                  out["source"] == "fallback" and "not available to the AI"
                  in out.get("note", ""), json.dumps(out))
            # Show design: the model designs for the rig's own design roles.
            design_reply = {"concepts": [
                {"name": "Ember", "tagline": "slow warm build",
                 "palette": ["#ff8a2a", "#ffd9a8", "nonsense"],
                 "cues": [
                     {"name": "Open", "fade_s": 3, "hold_s": 0,
                      "looks": [{"role": "par", "hex": "#ff8a2a", "level": 60},
                                {"role": "laser", "hex": "#00ff00", "level": 100}]},
                     {"name": "Peak", "fade_s": 999, "hold_s": -5,
                      "looks": [{"role": "par", "hex": "#ffd9a8", "level": 140}]}]},
                {"name": "Empty", "cues": [{"name": "x", "looks": []}]}]}
            console_ai.llm.chat = lambda messages, tools=None, tool_choice=None, temperature=0.3: {
                "tool_calls": [{"function": {"name": "design",
                                             "arguments": json.dumps(design_reply)}}]}
            gen = console_ai.generate("a warm acoustic set", eng=e)
            concepts = gen["design"]["concepts"]
            check("the AI designs concepts for the patched rig",
                  gen["source"] == "llm" and len(concepts) == 1
                  and concepts[0]["name"] == "Ember", json.dumps(gen)[:300])
            cue1, cue2 = concepts[0]["cues"]
            check("roles the rig does not have are dropped, bad colours too",
                  set(cue1["intensity"]) == {"par"}
                  and [p["hex"] for p in concepts[0]["palette"]] == ["#ff8a2a", "#ffd9a8"],
                  json.dumps(concepts[0])[:300])
            check("levels and timings are clamped to what a desk can do",
                  cue2["intensity"]["par"] == 100 and cue2["fade_s"] == 30
                  and cue2["hold_s"] == 0, json.dumps(cue2))
            imported = e.act("import_show", concept=concepts[0], playback=2)
            first = e.playbacks[1]["stack"][0]["values"]
            check("a designed concept loads as cues that light the PARs",
                  imported["ok"] and len(e.playbacks[1]["stack"]) == 2
                  and all(row.get("dimmer", 0) > 0 for row in first.values()),
                  json.dumps(imported)[:200])
            console_ai.llm.chat = lambda messages, tools=None, tool_choice=None, temperature=0.3: {
                "content": "sorry, no json here"}
            out = console_ai.plan("blackout")
            check("LLM garbage falls back to the compiler",
                  out.get("source") == "fallback" and out["steps"]
                  and out.get("note"), json.dumps(out))
        finally:
            console_ai.llm.chat = original_chat

        # --- show from a prompt -----------------------------------------
        taken = console_ai.extract_brief(
            "techno club night, fast pace, red and blue, avoid green",
            offline=True)
        brief = taken["brief"]
        check("brief: pace fast", brief["pace"] == "fast", json.dumps(brief))
        check("brief: colours red + blue",
              "red" in brief["colours"] and "blue" in brief["colours"],
              str(brief["colours"]))
        check("brief: banned colour not collected",
              "green" not in brief["colours"], str(brief["colours"]))
        check("brief: avoid keeps green", "green" in brief["avoid"].lower(),
              str(brief["avoid"]))
        check("brief: event club night", brief["event"] == "club night",
              str(brief["event"]))

        gen = console_ai.generate("wedding, slow and elegant, warm white",
                                  variant=0, offline=True)
        design = gen["design"]
        check("generate -> design concepts",
              design.get("ok") and len(design.get("concepts", [])) >= 2
              and gen["source"] == "fallback",
              json.dumps(design)[:200])
        check("generate brief picked wedding + slow",
              gen["brief"]["event"] == "wedding"
              and gen["brief"]["pace"] == "slow", json.dumps(gen["brief"]))
        check("generate brief keeps colour list",
              any("warm white" in c or "white" in c
                  for c in gen["brief"]["colours"]), str(gen["brief"]))
    finally:
        config.LLM_API_KEY = saved_key


def test_sacn(tmp: Path) -> None:
    import socket as _socket
    import struct as _struct

    from app import artnet, sacn

    print("sACN (E1.31) transport")
    data = bytes((i * 7) % 256 for i in range(512))
    pkt = sacn.build_sacn(1, data, sequence=9)
    check("packet = 126-byte header + 512 slots", len(pkt) == 638, str(len(pkt)))
    check("preamble + postamble", pkt[0:4] == b"\x00\x10\x00\x00", pkt[0:4].hex())
    check("ACN packet identifier", pkt[4:16] == b"ASC-E1.17\0\0\0", repr(pkt[4:16]))
    check("root vector = DATA (4)", _struct.unpack_from(">I", pkt, 18)[0] == 4)
    check("framing vector = DATA (2)", _struct.unpack_from(">I", pkt, 40)[0] == 2)
    check("DMP vector/type 0x02/0xA1", pkt[117] == 0x02 and pkt[118] == 0xA1)
    check("first property / increment 0 / 1",
          _struct.unpack_from(">H", pkt, 119)[0] == 0
          and _struct.unpack_from(">H", pkt, 121)[0] == 1)
    check("property count = start code + 512",
          _struct.unpack_from(">H", pkt, 123)[0] == 513)
    check("DMX start code 0", pkt[125] == 0, str(pkt[125]))
    for off in (16, 38, 115):
        flags = _struct.unpack_from(">H", pkt, off)[0]
        check(f"layer PDU length @{off} (flags 7 | {len(pkt) - off})",
              flags == 0x7000 | (len(pkt) - off), f"{flags:#06x}")
    check("source name JARVIS", pkt[44:52].split(b"\0")[0] == b"JARVIS")
    check("priority default 100", pkt[108] == 100, str(pkt[108]))
    check("sequence 9", pkt[111] == 9, str(pkt[111]))
    check("options 0 (live stream)", pkt[112] == 0)
    check("destination universe big-endian 1",
          _struct.unpack_from(">H", pkt, 113)[0] == 1)
    check("payload byte-identical", bytes(pkt[126:]) == data)

    # one universe config drives BOTH transports (wire = port address + 1)
    check("universe 1 -> wire 1", sacn.sacn_universe(1, 0) == 1)
    check("net shifts by 256 like Art-Net", sacn.sacn_universe(1, 1) == 257)
    admx = artnet.build_artdmx(7, data, sequence=3, net=2)
    pa = (admx[15] << 8) | admx[14]
    check("same config as the Art-Net port address",
          sacn.sacn_universe(7, 2) == pa + 1, f"pa={pa}")
    check("universe boundary 63999 accepted",
          sacn.sacn_universe(63999, 0) == 63999)
    check("net overflow past 63999 rejected",
          _valueerror(lambda: sacn.sacn_universe(63999, 1)))
    check("net 1 + universe 63743 = 63999 accepted",
          sacn.sacn_universe(63743, 1) == 63999)
    check("net 1 + universe 63744 = 64000 rejected",
          _valueerror(lambda: sacn.sacn_universe(63744, 1)))
    for bad in (0, -1, 64000, "x"):
        check(f"universe {bad!r} rejected",
              _valueerror(lambda b=bad: sacn.build_sacn(b, data)))
    check("net 128 rejected",
          _valueerror(lambda: sacn.build_sacn(1, data, net=128)))
    check("sequence 0 accepted", sacn.build_sacn(1, data, sequence=0)[111] == 0)
    check("sequence 255 accepted",
          sacn.build_sacn(1, data, sequence=255)[111] == 255)
    check("sequence 256 rejected",
          _valueerror(lambda: sacn.build_sacn(1, data, sequence=256)))
    check("sequence -1 rejected",
          _valueerror(lambda: sacn.build_sacn(1, data, sequence=-1)))
    check("priority 0 accepted", sacn.build_sacn(1, data, priority=0)[108] == 0)
    check("priority 200 accepted",
          sacn.build_sacn(1, data, priority=200)[108] == 200)
    check("priority 201 rejected",
          _valueerror(lambda: sacn.build_sacn(1, data, priority=201)))
    check("priority -1 rejected",
          _valueerror(lambda: sacn.build_sacn(1, data, priority=-1)))
    check("empty frame rejected", _valueerror(lambda: sacn.build_sacn(1, b"")))
    check("513 slots rejected",
          _valueerror(lambda: sacn.build_sacn(1, bytes(513))))
    check("1 slot accepted", len(sacn.build_sacn(1, b"\x01")) == 127)
    check("bad CID rejected",
          _valueerror(lambda: sacn.build_sacn(1, data, cid="not-hex")))

    dec = sacn.decode_sacn(pkt)
    check("decode: universe/sequence/priority round-trip",
          dec["universe"] == 1 and dec["sequence"] == 9
          and dec["priority"] == 100,
          str({k: dec[k] for k in ("universe", "sequence", "priority")}))
    check("decode: payload round-trip", bytes(dec["data"]) == data)
    check("decode: source name + stable CID",
          dec["source_name"] == "JARVIS" and len(dec["cid"]) == 32
          and sacn.default_cid() == sacn.default_cid(), dec["cid"])
    broken = {
        "too short": pkt[:100],
        "bad preamble": b"\x00\x11" + pkt[2:],
        "not ACN": pkt[:4] + bytes(12) + pkt[16:],
        "not DATA root": pkt[:18] + bytes(4) + pkt[22:],
        "start code 1": pkt[:125] + b"\x01" + pkt[126:],
    }
    for label, bad in broken.items():
        check(f"decode rejects {label}",
              _valueerror(lambda b=bad: sacn.decode_sacn(b)))

    # sender: per-universe sequences, wrap, dry-run, real socket delivery
    snd = sacn.SacnSender("127.0.0.1", 5568, 0, dry_run=True)
    seqs = [snd.next_sequence(1) for _ in range(258)]
    check("sequence starts 0, wraps after 255",
          seqs[0] == 0 and seqs[255] == 255 and seqs[256] == 0
          and seqs[257] == 1, str(seqs[254:258]))
    check("sequence counters are per-universe",
          snd.next_sequence(2) == 0 and snd.next_sequence(1) == 2)
    check("dry run: no wire, counts simulated",
          snd.send(1, data) is False
          and snd.stats()["simulated_frames"] == 1
          and snd.stats()["frames_sent"] == 0, str(snd.stats()))
    check("stats exposes transport + priority",
          snd.stats()["transport"] == "sacn"
          and snd.stats()["priority"] == 100, str(snd.stats()))
    check("sender priority out of range rejected",
          _valueerror(lambda: sacn.SacnSender("127.0.0.1", 5568, 0,
                                              dry_run=True, priority=201)))

    rx = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    rx.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(3.0)
    port = rx.getsockname()[1]
    wire = sacn.SacnSender("127.0.0.1", port, 0, dry_run=False, priority=77)
    try:
        ok5, ok6 = wire.send(5, data), wire.send(6, data)
        got = [rx.recvfrom(2048)[0] for _ in range(2)]
        decs = [sacn.decode_sacn(g) for g in got]
        check("UDP loopback delivered both frames",
              ok5 is True and ok6 is True
              and wire.stats()["frames_sent"] == 2, str(wire.stats()))
        check("multi-universe round-trip with priority",
              {d["universe"] for d in decs} == {5, 6}
              and all(bytes(d["data"]) == data for d in decs)
              and all(d["priority"] == 77 for d in decs),
              str([(d["universe"], d["priority"]) for d in decs]))
    finally:
        wire.close()
        rx.close()
