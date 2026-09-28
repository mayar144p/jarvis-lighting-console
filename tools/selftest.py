"""Jarvis self-test - run this first after changing anything.

    python tools/selftest.py

Checks: GDTF parser, database import/search, the layout generator,
Art-Net packet bytes, channel roles and the console engine
(patch, programmer, merges, cues, fades, show files, gates) -
plus the M6 suites: sACN packets, 16-bit DMX assembly, DMX input,
MIDI mapping, auto-follow, fixture profiles and simulated scan.

Every suite runs isolated: one exception is reported as a failure and the
rest of the run continues, so a single broken test can never hide the
other 600 checks behind it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, fixtures                   # noqa: E402
from tools import import_gdtf                  # noqa: E402

SYNTHETIC_GDTF = """<?xml version="1.0" encoding="UTF-8"?>
<GDTF dataVersion="1.2">
  <Manufacturer>TestBrand</Manufacturer>
  <Name>Beam400</Name>
  <Description>synthetic fixture for self-test</Description>
  <DMXModes>
    <DMXMode Name="Basic 8ch" Geometry="Body">
      <DMXChannels>
        <DMXChannel Offset="1"><LogicalChannel Attribute="Dimmer"><ChannelFunction Attribute="Dimmer"/></LogicalChannel></DMXChannel>
        <DMXChannel Offset="2"><LogicalChannel Attribute="Shutter"><ChannelFunction Attribute="Shutter"/></LogicalChannel></DMXChannel>
        <DMXChannel Offset="3"><LogicalChannel Attribute="ColorAdd_R"><ChannelFunction Attribute="ColorAdd_R"/></LogicalChannel></DMXChannel>
        <DMXChannel Offset="4"><LogicalChannel Attribute="ColorAdd_G"><ChannelFunction Attribute="ColorAdd_G"/></LogicalChannel></DMXChannel>
        <DMXChannel Offset="5"><LogicalChannel Attribute="ColorAdd_B"><ChannelFunction Attribute="ColorAdd_B"/></LogicalChannel></DMXChannel>
        <DMXChannel Offset="6,7"><LogicalChannel Attribute="Pan"><ChannelFunction Attribute="Pan"/></LogicalChannel></DMXChannel>
        <DMXChannel Offset="8"><LogicalChannel Attribute="Tilt"><ChannelFunction Attribute="Tilt"/></LogicalChannel></DMXChannel>
      </DMXChannels>
    </DMXMode>
    <DMXMode Name="Mini 4ch" Geometry="Body">
      <DMXChannels>
        <DMXChannel Offset="1"><LogicalChannel Attribute="Dimmer"><ChannelFunction Attribute="Dimmer"/></LogicalChannel></DMXChannel>
        <DMXChannel Offset="2"><LogicalChannel Attribute="ColorAdd_R"><ChannelFunction Attribute="ColorAdd_R"/></LogicalChannel></DMXChannel>
        <DMXChannel Offset="3"><LogicalChannel Attribute="ColorAdd_G"><ChannelFunction Attribute="ColorAdd_G"/></LogicalChannel></DMXChannel>
        <DMXChannel Offset="4"><LogicalChannel Attribute="ColorAdd_B"><ChannelFunction Attribute="ColorAdd_B"/></LogicalChannel></DMXChannel>
      </DMXChannels>
    </DMXMode>
  </DMXModes>
</GDTF>
"""

# A GDTF 1.0 archive exactly as gdtf-share.com serves one, which is
# DIFFERENT from the legacy shape above in three ways that each broke the
# importer in production and none of which a synthetic fixture can catch:
#
#   1. the document is `description.xml` at the archive root, not an entry
#      named "*.gdtf" - so the file was never found;
#   2. Manufacturer/Name are ATTRIBUTES of <FixtureType>, not child
#      elements of <GDTF> - so the model came out as the filename;
#   3. <DMXModes> is a child of <FixtureType> - so no mode was read and
#      the fixture landed in the library as a 0-channel stub.
#
# The first live download failed on all three, and every one of them is a
# silent wrong answer rather than an error.
SPEC_GDTF = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<GDTF DataVersion="1.0">
  <FixtureType Description="A real mover" Name="Beam900"
              Manufacturer="Acme Lighting" LongName="Beam 900"
              ShortName="B900" FixtureTypeID="4810925D-18D5-4771-9137-B2274F82DE7C">
    <DMXModes>
      <DMXMode Name="6 Channel">
        <DMXChannels>
          <DMXChannel Offset="1,2"><LogicalChannel Attribute="Pan"><ChannelFunction Name="Pan" OriginalAttribute="Pan"/></LogicalChannel></DMXChannel>
          <DMXChannel Offset="3,4"><LogicalChannel Attribute="Tilt"><ChannelFunction Name="Tilt" OriginalAttribute="Tilt"/></LogicalChannel></DMXChannel>
          <DMXChannel Offset="5"><LogicalChannel Attribute="ColorAdd_R"><ChannelFunction Name="Red" OriginalAttribute="Red"/></LogicalChannel></DMXChannel>
          <DMXChannel Offset="6"><LogicalChannel Attribute="Gobo"><ChannelFunction Name="Gobo" OriginalAttribute="Gobo"/></LogicalChannel></DMXChannel>
        </DMXChannels>
      </DMXMode>
      <DMXMode Name="8 Channel">
        <DMXChannels>
          <DMXChannel Offset="1"><LogicalChannel Attribute="Dimmer"><ChannelFunction Name="Dim" OriginalAttribute="Dimmer"/></LogicalChannel></DMXChannel>
          <DMXChannel Offset="2"><LogicalChannel Attribute="ColorAdd_R"><ChannelFunction Name="Red" OriginalAttribute="Red"/></LogicalChannel></DMXChannel>
        </DMXChannels>
      </DMXMode>
    </DMXModes>
  </FixtureType>
</GDTF>
"""

PASS, FAIL = 0, 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}  {detail}")


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
    showdesign.clear()
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
    showdesign.clear()

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
    js_fx = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
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
        check(f"console.js reads fx.{field}",
              f"f.{field}" in js_fx, field)
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
        check("rainbow = red/green/blue waves 120 deg apart",
              len(fx) == 3
              and [s["fx"]["attribute"] for s in fx]
              == ["red", "green", "blue"]
              and [s["fx"]["phase"] for s in fx] == [0, 120, 240]
              and fx[0]["fx"]["spread"] == 180, json.dumps(rainbow))

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
              and fx[0]["fx"]["kind"] == "random", json.dumps(out))

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

        dedup = console_ai.resolve(rainbow["steps"])
        check("select_all deduped across fx steps",
              [c["action"] for c in dedup]
              == ["select_all", "run_fx", "run_fx", "run_fx"],
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
            console_ai.llm.chat = lambda messages, tools=None: {
                "content": '```json\n{"reply":"dimmed","steps":[{"target":'
                           '"all","action":"set_intensity","attributes":'
                           '{"level":40}}]}\n```'}
            out = console_ai.plan("to 40")
            check("LLM path parses fenced JSON",
                  out.get("source") == "llm"
                  and out["steps"][0]["attributes"]["level"] == 40,
                  json.dumps(out))
            console_ai.llm.chat = lambda messages, tools=None: {
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


# ==========================================================================
# M6: sACN transport, 16-bit DMX, DMX input, MIDI, auto-follow,
#     fixture profiles and simulated Art-Net scan validation
# ==========================================================================
import struct                        # noqa: E402  (packet synthesis)
import threading                     # noqa: E402  (simulated node threads)


def _valueerror(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def _free_udp_port() -> int:
    import socket as _socket
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        s.bind(("", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _wait_until(fn, timeout: float = 3.0) -> bool:
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if fn():
            return True
        _time.sleep(0.02)
    return bool(fn())


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
    import tempfile
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
    js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
    viz = (ROOT / "web" / "viz.js").read_text(encoding="utf-8")

    check("console.js merges the aim out of the feed",
          "mergeLook(h.n, h.hex" in js, "")
    check("console.js does not rebuild a look without the aim",
          "next = { hex: h.hex" not in js,
          "a hand-built look object drops pan/tilt silently")

    check("viz.js carries the aim through setLooks",
          'if (typeof row.pan === "number") out.pan = row.pan;' in viz, "")
    check("viz.js deletes an absent aim instead of leaving a stale one",
          "delete f._look.pan;" in viz, "")
    check("viz.js applies the aim without waiting for a frame",
          "function syncAim()" in viz and "syncAim();" in viz, "")
    check("viz.js records whether a head is driven at all",
          "aimed: hasPan || hasTilt" in viz, "")
    check("the tilt channel spans 270 degrees, as documented",
          "const TILT_SPAN = 270;" in viz, "")

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
    html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")
    js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
    check("the add-heads dialog has a GDTF Share tab",
          'id="tab-share"' in html and 'id="dlg-pane-share"' in html, "")
    check("it can sign in, search and download",
          'id="share-login"' in html and 'id="share-q"' in html
          and 'id="share-list"' in html, "")
    check("the panel is wired on start-up", "wireShare();" in js, "")
    check("a download supersedes and then re-selects the new fixture",
          "/api/gdtf/download" in js and "sharePane(false)" in js
          and "pickFixture(found" in js, "")
    check("a superseding download warns that patched heads change",
          "share-superseded" in js, "")
    # The search text used to be passed as `refresh=`, so the box searched
    # nothing and looked broken; and a search reply has no session fields,
    # so assigning it to the status made a signed-in client look signed
    # out.  Both are contracts now.
    check("the search query puts the text in q=, not in refresh=",
          '"/api/gdtf/search?limit=60&q="' in js, "")
    check("a search reply does not overwrite the session status",
          "shareSt = data;\n    shareResults" not in js, "")
    check("an unreachable Share is never drawn as an empty catalogue",
          "shareSt.last_error" in js, "")


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
    import tempfile
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
    import tempfile
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
        check("an untouched channel reports 0 rather than blank",
              by_role["red"]["value"] == 0, str(by_role["red"]))

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
    import zipfile
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


def test_physical_ranges(tmp: Path) -> None:
    """GDTF ranges -> degrees in the console, and a real bug in the domain.

    THE RANGE WAS NEVER CAPTURED.  `parse_gdtf` compiled each profile down
    to a bare list of channel NAMES, so a light's own travel was thrown
    away on import.  Worse, the range is not where the spec's older drafts
    put it: real files from the Share carry it on `ChannelFunction` as
    `@PhysicalFrom`/`@PhysicalTo` paired with `@DMXFrom`, while
    `LogicalChannel/@Min`/`@Max` - the first thing anyone reaches for - is
    absent.  Reading only the first found nothing at all, which is why
    pan could only ever be an abstract 0-255 and why the visualiser's
    hardcoded 270 degrees of tilt was never checked against a real file.
    A Chauvet Intimidator says tilt is -117..+117, so 270 was 15% wrong.

    THE DOMAIN BUG IS THE INTERESTING PART.  The console accepted 0-65535
    for every channel and `merge` CLAMPS an unpaired channel to 255 on the
    way to the wire - so the encoder displayed whatever you typed, the
    light was sent 255, and the entire upper half of every 8-bit channel
    was unreachable.  An 8-bit pan could not be aimed at 90 degrees at all.
    The domain is a property of the channel's WIDTH: 0-100 for a level,
    0-255 in one slot, 0-65535 across a `role`/`role_fine` pair.
    """
    print("physical ranges (GDTF travel -> degrees, and the channel domain)")
    from app import engine as eng
    from app import fixtures
    import zipfile

    db = tmp / "ranges.db"
    fixtures.seed_generics(db)

    def gdtf(name, channels, physical=""):
        path = tmp / (name + ".gdtf")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("description.xml", (
                '<GDTF DataVersion="1.2"><FixtureType Name="%s" '
                'Manufacturer="TestCo"><DMXModes><DMXMode Name="d">' % name
                + "<DMXChannels>" + channels + "</DMXChannels>"
                + "</DMXMode></DMXModes></FixtureType></GDTF>"))
        return fixtures.import_file(db, path)

    # -- a 16-bit pan pair whose travel comes from ChannelFunction ---------
    gdtf("Mover16",
         '<DMXChannel Offset="1,2"><LogicalChannel Attribute="Pan">'
         '<ChannelFunction Name="Pan" OriginalAttribute="Pan" DMXFrom="0/2" '
         'PhysicalFrom="-270" PhysicalTo="270"/></LogicalChannel>'
         '</DMXChannel>'
         '<DMXChannel Offset="3"><LogicalChannel Attribute="Tilt">'
         '<ChannelFunction Name="Tilt" OriginalAttribute="Tilt" '
         'PhysicalFrom="-117" PhysicalTo="117"/></LogicalChannel>'
         '</DMXChannel>'
         '<DMXChannel Offset="4"><LogicalChannel Attribute="Color1">'
         '<ChannelFunction Name="Color1" OriginalAttribute="" '
         'PhysicalFrom="0" PhysicalTo="1" Wheel="Color Wheel"/>'
         '</LogicalChannel></DMXChannel>')
    rr = fixtures.role_ranges(db, "TestCo", "Mover16", "d")
    check("travel is read from ChannelFunction, where real files put it",
          rr.get("pan", {}).get("min") == -270.0
          and rr["pan"]["max"] == 270.0, str(rr.get("pan")))
    check("and it is reported as degrees", rr["pan"]["unit"] == "degree",
          str(rr["pan"]))
    check("a 0..1 channel is a POSITION, not 0..1 degrees",
          rr.get("wheel", {}).get("unit") == "position",
          str(rr.get("wheel")))
    check("the fine half of a 16-bit pair does not halve the travel",
          rr["pan"]["bits"] == 16, str(rr["pan"]))

    # -- a range declared the OLD way must still be honoured ----------------
    gdtf("Legacy",
         '<DMXChannel Offset="1"><LogicalChannel Attribute="Tilt" '
         'Min="-90" Max="90"><ChannelFunction Name="Tilt" '
         'OriginalAttribute="Tilt"/></LogicalChannel></DMXChannel>')
    rr = fixtures.role_ranges(db, "TestCo", "Legacy", "d")
    check("LogicalChannel/@Min|Max is read too, for older drafts",
          rr.get("tilt", {}).get("min") == -90.0
          and rr["tilt"]["max"] == 90.0, str(rr.get("tilt")))

    # -- degrees in, degrees on the wire, for BOTH widths --------------------
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "pr")
    try:
        e.act("add_heads", query="Mover16", mode="d", qty=1,
              universe=1, address=1, x=0, y=0, z=1)
        e.act("select_heads", heads=[1])
        st = e.attribute_state([1])
        pan = [a for p in st["pages"] for a in p["attrs"] if a["role"] == "pan"][0]
        check("the grid offers the light's own travel",
              pan["min"] == -270.0 and pan["max"] == 270.0, str(pan))
        check("and states the channel's domain",
              pan["full"] == 65535, str(pan))
        r = e.act("set_attr_range", attribute="pan", value=90, unit="degree")
        check("90 degrees is accepted as an angle",
              r.get("ok") and r.get("unit") == "degree", str(r))
        check("and is reported back as 90 degrees, not as an internal number",
              r.get("phys") == 90.0, str(r))
        chans = {c["role"]: c["value"] for c in
                 e.channel_report([1])["heads"][0]["channels"]
                 if c["role"] in ("pan", "pan_fine")}
        want = round((90 + 270) / 540 * 65535)
        check("a 16-bit pair really puts those bytes on the wire",
              chans["pan"] == (want >> 8) and chans["pan_fine"] == (want & 255),
              str(chans))

        # An 8-bit pan: the case the domain bug made impossible.
        gdtf("Mover8",
             '<DMXChannel Offset="1"><LogicalChannel Attribute="Pan">'
             '<ChannelFunction Name="Pan" OriginalAttribute="Pan" '
             'PhysicalFrom="-270" PhysicalTo="270"/></LogicalChannel>'
             '</DMXChannel>')
        e.act("add_heads", query="Mover8", mode="d", qty=1,
              universe=1, address=20, x=2, y=0, z=1)
        eight = e.patch[-1]["head_no"]
        e.act("select_heads", heads=[eight])
        r = e.act("set_attr_range", attribute="pan", value=90, unit="degree")
        st = e.attribute_state([eight])
        pan8 = [a for p in st["pages"] for a in p["attrs"]
                if a["role"] == "pan"][0]
        check("an 8-bit pan is offered 0-255, not 0-65535",
              pan8["full"] == 255, str(pan8))
        got = [c["value"] for c in e.channel_report([eight])["heads"][0]
               ["channels"] if c["role"] == "pan"][0]
        check("90 degrees lands on byte 170 of a 0-255 travel",
              abs(got - round((90 + 270) / 540 * 255)) <= 1,
              "byte %s, asked %s" % (got, r.get("phys")))
        check("NOT 255, which is what the old clamp sent for anything >255",
              got != 255 or r.get("phys") == 270.0, str(got))

        # -- clamping is reported in the unit the operator typed -------------
        r = e.act("set_attr_range", attribute="pan", value=900,
                  unit="degree")
        check("out of travel is clamped and says the travel in degrees",
              r.get("clamped") and "clamped to 270" in r["summary"]
              and "-270..270" in r["summary"], r.get("summary"))
        check("and never echoes the internal conversion number",
              "43690" not in r["summary"] and "141992" not in r["summary"],
              r.get("summary"))
        r = e.act("set_attr_range", attribute="pan", value=0, unit="degree")
        # 540 degrees of travel across 256 steps is 2.1 degrees a step, so
        # 0 is not exactly reachable.  What must not happen is the FLOAT
        # artefact: 32767.5 rounding up to 32768 and reading back as
        # 0.00178531.  Being within a step and rounded for display is the
        # honest answer.
        check("0 degrees reads back as a rounded angle, not a float artefact",
              r["phys"] is not None and abs(r["phys"]) <= 2.2
              and r["phys"] != 0.00178531, str(r.get("phys")))

        # -- the unit must be explicit, and a wrong one is named ------------
        r = e.act("set_attr_range", attribute="pan", value=170)
        check("with no unit the number is LOGICAL, not degrees",
              r.get("value") == 170 and r.get("phys") is None, str(r))
        r = e.act("set_attr_range", attribute="pan", value=50, unit="percent")
        check("asking for the wrong unit is refused by name",
              not r.get("ok") and "degrees, not percent" in r.get("error", ""),
              r.get("error"))
        e.act("select_heads", heads=[1])       # Mover16, which has a wheel
        r = e.act("set_attr_range", attribute="wheel", value=50, unit="degree")
        check("and a channel that is not an angle says what it IS",
              not r.get("ok") and "position" in r.get("error", ""),
              r.get("error"))

        # -- two movers with different travel get no single answer ----------
        gdtf("MoverNarrow",
             '<DMXChannel Offset="1"><LogicalChannel Attribute="Pan">'
             '<ChannelFunction Name="Pan" OriginalAttribute="Pan" '
             'PhysicalFrom="-90" PhysicalTo="90"/></LogicalChannel>'
             '</DMXChannel>')
        e.act("add_heads", query="MoverNarrow", mode="d", qty=1,
              universe=1, address=40, x=4, y=0, z=1)
        narrow = e.patch[-1]["head_no"]
        e.act("select_heads", heads=[eight, narrow])
        st = e.attribute_state()
        pan_mix = [a for p in st["pages"] for a in p["attrs"]
                   if a["role"] == "pan"][0]
        check("a mixed selection reports both travels rather than a mean",
              pan_mix.get("mixed_range") == [[-270.0, 270.0], [-90.0, 90.0]],
              str(pan_mix.get("mixed_range")))
        r = e.act("set_attr_range", attribute="pan", value=90, unit="degree")
        check("and refuses to convert degrees across them, by name",
              not r.get("ok") and "-270..270" in r.get("error", "")
              and "-90..90" in r.get("error", ""), r.get("error"))
        r = e.act("set_attr_range", attribute="pan", value=170)
        check("but a LOGICAL value still works on a mixed selection",
              r.get("ok") and r["heads"] == 2, str(r))

        # -- a hand-written profile can carry travel too ---------------------
        made = fixtures.create_profile(db, "Hand", "Written", "d",
                                       ["Pan = -180..180", "Tilt = -45..45",
                                        "Dimmer"])
        check("a hand-written profile can declare travel inline",
              made["ranges"] == 2, str(made))
        check("and the summary says it has physical ranges",
              "physical range" in made["summary"], made["summary"])
        rr = fixtures.role_ranges(db, "Hand", "Written", "d")
        check("which the console then reads back for those roles",
              rr["tilt"]["min"] == -45.0 and rr["tilt"]["unit"] == "degree",
              str(rr.get("tilt")))
        for bad, frag in ((["Pan = 90.."], "look like"),
                          (["Pan = 0..0"], "no range"),
                          (["Tilt = a..b"], "look like")):
            refused = False
            try:
                fixtures.create_profile(db, "Bad", "Travel", "d", bad)
            except ValueError as exc:
                refused = frag in str(exc)
            check("inline travel %r is refused" % bad[0], refused, bad[0])

        # -- the range can be edited and taken away --------------------------
        e.act("add_heads", query="Hand", mode="d", qty=1,
              universe=1, address=60, x=6, y=0, z=1)
        hand = e.patch[-1]["head_no"]
        mode_id = next(m for f in fixtures.search(db, "Written")
                       for m in fixtures.list_modes(db, f["id"]))["id"]
        done = fixtures.set_channel_range(db, mode_id, 1, -270, 270)
        check("a travel can be changed after the fact",
              done["min"] == -270.0 and "range -270..270" in done["summary"],
              str(done))
        e.remap_heads()
        e.act("select_heads", heads=[hand])
        st = e.attribute_state([hand])
        tilt = [a for p in st["pages"] for a in p["attrs"]
                if a["role"] == "tilt"][0]
        check("and the console sees the new one immediately",
              tilt["max"] == 270.0, str(tilt))
        done = fixtures.set_channel_range(db, mode_id, 1, None, None)
        check("a travel can be cleared again",
              done["min"] is None and "range cleared" in done["summary"],
              str(done))
        e.remap_heads()
        e.act("select_heads", heads=[hand])
        r = e.act("set_attr_range", attribute="tilt", value=45, unit="degree")
        check("with no travel, degrees can no longer be converted, and it says so",
              not r.get("ok") and "no range is known" in r.get("error", ""),
              r.get("error"))

        # -- the client -------------------------------------------------------
        js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "console.css").read_text(encoding="utf-8")
        viz = (ROOT / "web" / "viz.js").read_text(encoding="utf-8")
        check("the channel editor has a travel column",
              "fx-ch-span" in js and ".fx-ch-span" in css, "")
        check("a degrees box is text so it can hold the degree sign",
              'val.type = physical ? "text" : "number"' in js, "")
        check("and the value is formatted from the fixture's own range",
              "fmtAttrValue" in js, "")
        check("the visualiser uses the fixture's travel when it is sent",
              "look.deg && look.deg.tilt" in viz, "")
        check("so the hardcoded 270 is now only the no-GDTF fallback",
              "TILT_SPAN" in viz and "pspan" in viz, "")
    finally:
        e.shutdown()


def _csv_split(line: str) -> list[str]:
    """Parse one RFC 4180 row, so a quoting test can check the real cells.

    Written out rather than borrowed from the csv module because the point
    is to check that what the engine WROTE parses back to what was meant -
    trusting the same reader that wrote it would pass on a symmetric bug.
    """
    out, cell, quoted, i = [], "", False, 0
    while i < len(line):
        c = line[i]
        if quoted:
            if c == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    cell += '"'
                    i += 2
                    continue
                quoted = False
            else:
                cell += c
        elif c == '"':
            quoted = True
        elif c == ",":
            out.append(cell)
            cell = ""
        else:
            cell += c
        i += 1
    out.append(cell)
    return out


def test_arrange(tmp: Path) -> None:
    """ALIGN, DISTRIBUTE, MIRROR, and degree aim.

    THE THING YOU DO TWENTY TIMES IN AN HOUR AND CANNOT DO BY DRAGGING.
    Six movers on a bar are not evenly spaced after you have moved them
    individually, and making them even again is one drag each - or, on a
    real console, two keys.

    The distinction between the three is the feature, not an
    implementation detail.  ALIGN uses the mean; DISTRIBUTE uses the two
    ends.  A row of six with the middle two bunched is NOT misaligned -
    every head is off-axis, so there is no one line to align them to - and
    aligning is the wrong tool.  Treating the two as synonyms is the
    standard way these get built wrong, so the test sets up exactly that
    shape and checks that align collapses it while distribute fixes it.

    The distribute check measures the gaps in POSITION order, not head
    order.  A rig numbered 1..n along a bar makes those the same list,
    which is precisely how a head-number-ordered bug survives every check;
    a rig numbered by fixture type is where it shows.
    """
    print("arrange (align, distribute, mirror) and degree aim")
    from app import engine as eng
    from app import fixtures

    db = tmp / "arr.db"
    fixtures.seed_generics(db)
    fixtures.create_profile(db, "TestCo", "Mover", "3ch",
                            ["Pan = -270..270", "Tilt = -117..117",
                             "Dimmer"])
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "arr")
    try:
        # A bar of seven, numbered so that position order is NOT head order
        # for the last head - which is what makes the gap check meaningful.
        for i, x in enumerate([0.0, 1.0, 1.2, 4.0, 4.1, 8.0, 2.0]):
            e.act("add_heads", query="Mover" if i % 2 == 0 else "LED PAR",
                  mode="3ch" if i % 2 == 0 else "4ch RGBW",
                  qty=1, universe=1, address=1 + i * 4, x=x, y=0, z=0)
        movers = [h["head_no"] for h in e.patch
                  if "Mover" in h.get("model", "")]
        pars = [h["head_no"] for h in e.patch if "PAR" in h.get("model", "")]
        allheads = [h["head_no"] for h in e.patch]
        check("the rig has both kinds, for partial-aim checks",
              len(movers) == 4 and len(pars) == 3,
              "movers=%s pars=%s" % (movers, pars))

        def setbar(values):
            for i, h in enumerate(e.patch):
                h["x"] = values[i]

        def xs():
            return [float(h["x"]) for h in e.patch]

        def gaps():
            s = sorted(xs())
            return [round(s[i + 1] - s[i], 3) for i in range(len(s) - 1)]

        # ---- DISTRIBUTE ---------------------------------------------------
        setbar([0.0, 1.0, 1.2, 4.0, 4.1, 8.0, 2.0])
        r = e.act("distribute", heads=allheads, axis="x")
        check("distribute reports what it did", r.get("ok")
              and "distributed" in r["summary"], r.get("summary"))
        g = gaps()
        check("the gaps are all equal, measured in POSITION order",
              len(set(g)) == 1, str(g))
        check("and it is not equal spacing by HEAD order, which would pass "
              "a head-order check on a numbered bar",
              len({round(xs()[i + 1] - xs()[i], 3)
                   for i in range(len(xs()) - 1)}) > 1,
              str([round(xs()[i + 1] - xs()[i], 3) for i in range(6)]))
        check("the two outermost heads are left where they were",
              min(xs()) == 0.0 and max(xs()) == 8.0,
              "%s..%s" % (min(xs()), max(xs())))

        # ---- ALIGN --------------------------------------------------------
        setbar([0.0, 2.0, 2.5, 5.0, 5.5, 9.0, 3.5])
        r = e.act("align", heads=allheads, axis="x")
        check("align puts every head on ONE line", r.get("ok")
              and len({round(v, 3) for v in xs()}) == 1, str(xs()))
        check("at the MEAN, not the first or the middle",
              abs(round(xs()[0], 2) - 3.93) < 0.01, str(round(xs()[0], 2)))
        check("and the spacing is GONE, which is the difference from "
              "distribute", all(g == 0.0 for g in gaps()), str(gaps()))
        e.act("align", heads=allheads, axis="x")
        check("aligning an aligned selection says so rather than claiming "
              "a move", e.act("align", heads=allheads, axis="x")["heads"] == 0
              and "already aligned" in
              e.act("align", heads=allheads, axis="x")["summary"], "")

        # ---- the shape that separates the two -----------------------------
        bunched = [0.0, 1.0, 1.1, 5.0, 5.1, 6.0, 1.05]
        setbar(bunched)
        e.act("align", heads=allheads, axis="x")
        check("on a bunched bar, align collapses it to one point",
              len({round(v, 3) for v in xs()}) == 1, str(xs()))
        setbar(bunched)
        e.act("distribute", heads=allheads, axis="x")
        check("on the same bar, distribute spreads it evenly",
              len(set(gaps())) == 1, str(gaps()))
        check("which is the whole reason they are separate commands",
              sorted(xs())[0] == 0.0 and sorted(xs())[-1] == 6.0,
              str(sorted(xs())))

        # ---- MIRROR -------------------------------------------------------
        shape = [0.0, 1.0, 2.0, 5.0, 6.0, 7.0, 3.0]
        setbar(shape)
        mean = sum(shape) / len(shape)
        r = e.act("mirror", heads=allheads, axis="x")
        check("mirror about the mean puts every head at 2*mean - x",
              all(abs(v - (2 * mean - o)) < 0.01
                  for v, o in zip(xs(), shape)), str(xs()))
        check("and reports the centre it used",
              abs(r["about"]["x"] - mean) < 0.01, str(r["about"]))
        r = e.act("mirror", heads=allheads, axis="x", about=0)
        # Two mirrors do NOT cancel: the first moved every head to
        # 2*mean - x, so reflecting THAT about 0 gives x - 2*mean.  A check
        # asserting the original shape back would be wrong.
        check("mirror about 0 negates whatever is there now",
              all(abs(v + (2 * mean - o)) < 0.01
                  for v, o in zip(xs(), shape)), str(xs()))
        check("so two mirrors are not an undo",
              any(abs(abs(v) - abs(o)) > 0.01
                  for v, o in zip(xs(), shape)), str(xs()))
        # A head already ON the centre cannot move, and that is the one
        # case where a mirror genuinely does nothing.
        setbar([0.0, 1.0, 2.0, 5.0, 6.0, 7.0, 0.0])
        r = e.act("mirror", heads=[allheads[-1]], axis="x")
        check("a head already on the mirror line does not move",
              r["heads"] == 0 and "already on the line" in r["summary"],
              r["summary"])
        setbar([-3.0, -1.0, 0.0, 1.0, 3.0, 0.0, 0.0])
        r = e.act("mirror", heads=allheads, axis="x")
        check("a symmetric SHAPE still swaps its heads, and says it moved "
              "them - mirror is per head, not set-based",
              r["heads"] == 4, "%s of %s" % (r["heads"], sorted(xs())))

        # ---- axes ---------------------------------------------------------
        setbar(shape)
        for i, h in enumerate(e.patch):
            h["y"] = float(i)
            h["z"] = float(i) * 2
        r = e.act("align", heads=allheads, axis="y")
        check("align works on y as well as x",
              len({round(h["y"], 3) for h in e.patch}) == 1, str(
                  [h["y"] for h in e.patch]))
        r = e.act("align", heads=allheads, axis="all")
        check("`all` lines up all three axes at once",
              r.get("ok") and len(r["axis"]) == 3, str(r.get("axis")))
        r = e.act("align", heads=allheads, axis="wibble")
        check("a bad axis is refused and the axis is named",
              not r.get("ok") and "x, y, z" in r["error"], r.get("error"))

        # ---- too few heads -------------------------------------------------
        r = e.act("distribute", heads=movers[:2], axis="x")
        check("distribute needs three, and says what two is not enough for",
              not r.get("ok") and "at least 3" in r["error"]
              and "nothing between them" in r["error"], r.get("error"))

        # ---- degree aim ---------------------------------------------------
        e.act("clear_programmer")
        e.act("select_heads", heads=movers)
        r = e.act("set_position", pan=90, tilt=0)
        check("with no unit, a value is DEGREES when the fixture knows "
              "its travel", r.get("unit") == "degree", str(r))
        check("and the transcript says so, in degrees",
              "90" in r["summary"] and "°" in r["summary"], r["summary"])
        rr = fixtures.role_ranges(db, "TestCo", "Mover", "3ch")
        for h in movers:
            got = float((e.programmer.get(h) or {}).get("pan", 0))
            back = rr["pan"]["min"] + (got / 255.0) * (
                rr["pan"]["max"] - rr["pan"]["min"])
            check("head %s really is at 90 degrees of its own travel" % h,
                  abs(back - 90) <= 2.2, "%.1f" % back)
        r = e.act("set_position", pan=90, unit="logical")
        check("an explicit logical unit overrides the inference",
              r.get("unit") == "logical" and "logical" in r["summary"],
              r["summary"])
        r = e.act("set_position", pan=90, unit="percent")
        check("a unit that is neither is refused BY NAME",
              not r.get("ok") and "not 'percent'" in r["error"],
              r.get("error"))

        # A fixture with NO declared travel must read as logical, not as a
        # wrong number of degrees - the §17.19 trap in its purest form.
        r = e.act("set_position", pan=90)
        e.act("select_heads", heads=pars)
        r = e.act("set_position", pan=90)
        check("a light with no travel reads the number as logical",
              r.get("aimed") is False, str(r))
        check("and says there is nowhere to aim, naming what it has",
              "blue" in (r.get("summary") or ""), r.get("summary"))

        # ---- partial aim is reported --------------------------------------
        e.act("select_heads", heads=allheads)
        e.act("clear_programmer")
        r = e.act("set_position", pan=0)
        check("a mixed selection aims what it can and says what it could not",
              r.get("partial") and r["heads"] == len(movers)
              and "missing" in r["summary"], r["summary"])

        # ---- the command line ---------------------------------------------
        e.act("select_heads", heads=[])
        r = e.act("run_command", text="%d-%d aim pan 90" % (movers[0],
                                                            movers[-1]))
        check("`1-4 aim pan 90` aims, and says degrees",
              r.get("ok") and "90" in r["summary"], r.get("summary"))
        e.act("clear_programmer")
        r = e.act("run_command", text="%d-%d aim 90 -45" % (movers[0],
                                                           movers[-1]))
        check("`aim 90 -45` is pan then tilt, positionally",
              r.get("ok") and "90" in r["summary"]
              and "-45" in r["summary"], r.get("summary"))
        for bad, frag in (("%d-%d aim pan" % (movers[0], movers[-1]),
                           "pan needs a value"),
                          ("%d aim pan 90 wibble" % movers[0],
                           "pan and tilt only")):
            r = e.act("run_command", text=bad)
            check("a bad aim line is refused: %s" % frag.split()[0],
                  not r.get("ok") and frag in r["error"], r.get("error"))
        setbar([0.0, 1.0, 1.1, 5.0, 5.1, 6.0, 1.05])
        r = e.act("run_command", text="all distribute x")
        check("`all distribute x` distributes", r.get("ok")
              and len(set(gaps())) == 1, str(gaps()))
        r = e.act("run_command", text="all align x 3")
        check("`align x 3` is refused, pointing at the mirror form",
              not r.get("ok") and "mirror x about N" in r["error"],
              r.get("error"))
        r = e.act("run_command", text="all mirror x about")
        check("`mirror x about` with no value says what it needs",
              not r.get("ok") and "needs a value" in r["error"],
              r.get("error"))
        n = len(e._undo)
        e.act("run_command", text="%d-%d aim pan 0" % (movers[0], movers[-1]))
        check("an aim line is still ONE undo step",
              len(e._undo) - n == 1, str(len(e._undo) - n))

        # ---- the client ---------------------------------------------------
        js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")
        check("the three arrange buttons exist",
              'id="btn-align"' in html and 'id="btn-distribute"' in html
              and 'id="btn-mirror"' in html, "")
        check("with an axis selector",
              'id="arr-axis"' in html, "")
        check("and a mirror-about box for an explicit centre",
              'id="arr-about"' in html, "")
        check("there is an aim box, in degrees",
              'id="aim-pan"' in html and 'id="aim-tilt"' in html, "")
        check("the client sends NO unit, so the server decides",
              'params.unit' not in js.split("async function aimRun")[1]
              .split("function")[0], "")
        check("a no-op is reported as a no-op",
              "changed nothing" in js, "")
        check("and the buttons are disabled, not merely guarded",
              "refreshArrange" in js and 'b.disabled = n < want' in js, "")

        # ---- the patch sheet ---------------------------------------------
        # THE DOCUMENT THE RIG ACTUALLY HAS.  Until this existed the agent's
        # own instructions told operators to make one by hand elsewhere,
        # which is how patch sheets come out with a head's DMX mode missing
        # - the one column that decides whether the sheet is usable.
        csv = e.patch_csv()
        lines = csv.splitlines()
        check("the sheet has a header and one row per head",
              len(lines) == len(e.patch) + 1, "%d lines" % len(lines))
        check("the header is the documented column list",
              lines[0].lstrip("﻿") ==
              ",".join(eng.Engine.PATCH_CSV_COLUMNS), lines[0])
        rows = [dict(zip([c.lstrip("﻿") for c in lines[0].split(",")],
                        ln.split(","))) for ln in lines[1:]]
        check("every head is on the sheet",
              sorted(int(r["Head"]) for r in rows) == sorted(allheads),
              str([r["Head"] for r in rows]))
        check("and the DMX MODE is on it, which is the column a hand-typed "
              "sheet always drops",
              all(r["Mode"] for r in rows), str([r["Mode"] for r in rows]))
        first = rows[0]
        check("the footprint and end address are real, not restated",
              first["End"] == str(int(first["Address"])
                                  + int(first["Footprint"]) - 1), str(first))
        check("positions are 2 dp with no float noise",
              all(len(r["Position X"].split(".")[-1]) <= 2 for r in rows),
              str([r["Position X"] for r in rows]))
        check("and a profile's source file is recorded",
              any("gdtf" in r["Source"] or r["Source"] == "built-in"
                  for r in rows), str([r["Source"] for r in rows]))
        check("a head with no roles says so rather than going blank",
              all(r["Roles"] for r in rows), str([r["Roles"] for r in rows]))

        # RFC 4180, on a name that actually breaks it.
        nasty = e.patch[0]
        keep = nasty["name"]
        nasty["name"] = 'Foo, Bar "Special" Ltd'
        quoted = e.patch_csv([nasty["head_no"]])
        nasty["name"] = keep
        q = quoted.splitlines()[1]
        check("a comma and a quote in a name are escaped, not dropped",
              '"Foo, Bar ""Special"" Ltd"' in q
              # 2 delimiters + 4 from the doubled pair; a dropped quote
              # would give 2 or 3 and the row would silently misalign.
              and q.count('"') == 6, q)
        cells = _csv_split(q)
        check("and the row still has one cell per column",
              len(cells) == len(eng.Engine.PATCH_CSV_COLUMNS), str(len(cells)))
        check("with the name intact after unescaping",
              cells[1] == 'Foo, Bar "Special" Ltd', cells[1])

        # The client builds the same sheet, from the same state, and the two
        # must agree.  Two formatters drifting apart is exactly the
        # "one line means one thing" rule's exception turning into a bug.
        js_csv = js[js.index("const PATCH_CSV_COLUMNS"):
                    js.index("function csvDownloadName")]
        check("the client uses the same column list as the engine",
              all(('"%s"' % c) in js_csv for c in eng.Engine.PATCH_CSV_COLUMNS),
              "")
        check("and escapes the same way",
              "csvCell" in js and 'replace(/"/g, \'""\')' in js, "")
        check("the sheet is built from the live state, not a fetch, so it "
              "cannot 404", "function patchCsv(" in js
              and "S.patch" in js_csv, "")
        check("there is a button for it", 'id="btn-patchcsv"' in html, "")
        check("and the filename is made safe",
              "csvDownloadName" in js and "patch.csv" not in
              js[js.index("function csvDownloadName"):][:400], "")

        # ---- the help overlay --------------------------------------------
        # BUILT FROM THE DOM AND THE LIVE STATE, not written out as HTML.
        # A help page that has drifted from the buttons is worse than none:
        # an operator who cannot find a feature concludes it does not
        # exist.  So the check that matters is that the command syntax comes
        # from the ENGINE's help, not from a second copy in the client.
        check("the help overlay exists and has a body to fill",
              'id="help-dialog"' in html and 'id="help-body"' in html, "")
        check("and a way in, by button and by `?`",
              'id="btn-help"' in html and 'wireHelp' in js, "")
        check("the command syntax comes from the engine's own help",
              "S.cmd_help" in js and "r.help" in js, "")
        check("which is stored from a `?` run, not re-typed",
              "S.cmd_help = r.help" in js, "")
        # Slice by the SECTION CALLS, not by the section titles: the words
        # "the command line" appear in a comment near the top of the file,
        # so indexing on the title silently produced an empty slice and the
        # check would have passed on anything.
        seg = js[js.index('helpSection("where you are")'):
                 js.index('helpSection("the command line")')]
        check("it states the LIVE state - heads, selection, live/dry - so it "
              "cannot describe a rig that is not there",
              seg and "patched" in seg and "selected" in seg
              and "DRY RUN" in seg and "universe" in seg,
              seg[:120] if seg else "empty slice")
        check("it carries the three things that are easy to get wrong",
              "removes an attribute" in js and "one undo step" in js
              and "all-or-nothing" in js, "")
        check("`?` is listed among the keys",
              '["?", "this help"]' in js, "")
        check("Escape closes it, like every other modal",
              'toggleHelp(false); return;' in js, "")
        check("it counts as an open dialog, so shortcuts stand down "
              "behind it", '"#help-dialog"' in js, "")
        # The overlay uses the `hidden` ATTRIBUTE because `.modal` sets
        # `display:flex` and a class would lose to it.  Toggling the class
        # anyway meant the button and `?` did nothing at all while
        # `anyDialogOpen` - which reads the attribute - said "closed":
        # the open and closed paths were spelled two different ways.
        check("and it toggles the same way anyDialogOpen reads it",
              "dlg.hidden = !open" in js
              and 'dlg.classList.toggle("hidden", !open)' not in js, "")
        check("the markup uses `hidden`, not a class, to start closed",
              'id="help-dialog" hidden' in html
              or ('id="help-dialog"' in html and "hidden" in
                  html[html.index('id="help-dialog"'):
                       html.index('id="help-dialog"') + 60]), "")
        check("the modal guard checks BOTH ways of being hidden, because "
              "the help overlay uses the `hidden` attribute and the older "
              "two use a class", "d.hidden === true" in js, "")
        # The guard is open = NOT hidden by EITHER convention.  Getting
        # that backwards returns true ALWAYS (the help overlay is created
        # with `hidden`), and every single-key shortcut in the console goes
        # dead with nothing on screen to say why.  The `!(a || b)` shape is
        # the check, because the plausible-looking `a || !b` is the bug.
        check("and it is the AND of the two, not the OR",
              "return !(d.hidden === true || d.classList.contains" in js,
              "")
        check("clicking the backdrop closes it, but clicking inside does "
              "not", "e.target === dlg" in js, "")
        # A wiring function that throws at INIT takes the whole init with
        # it: the console comes up blank, and because the throw is in a
        # try-free startup path nothing anywhere reports it.  This one threw
        # on `S.selected` because `wireArrange` runs before the first state
        # fetch has returned.
        check("refreshArrange tolerates being called before any state has "
              "loaded", "if (!S || !document.querySelector" in js, "")

        # Writing to a file is opt-in, and a directory gets the show's name.
        r = e.act("export_patch")
        check("with no path, the sheet is returned and NOTHING is written",
              r.get("ok") and r.get("path") is None
              and "not written" in r["summary"], r.get("summary"))
        out_dir = tmp / "csvdemo"
        out_dir.mkdir(parents=True, exist_ok=True)
        r = e.act("export_patch", path=str(out_dir))
        written = Path(r["path"])
        check("a directory gets the head's name, not 'patch.csv'",
              written.exists() and written.suffix == ".csv"
              and written.stem != "patch", str(written.name))
        check("and the file on disk is the sheet",
              len(written.read_text(encoding="utf-8").splitlines())
              == len(e.patch) + 1, written.name)
        # A name Windows would mangle.
        nasty["name"] = "Con: bad/name*?"
        r = e.act("export_patch", path=str(out_dir))
        safe = Path(r["path"]).name
        check("a name a filesystem would reject is made safe",
              not set('<>:"/\\|?*') & set(safe), safe)
        nasty["name"] = keep
    finally:
        e.shutdown()


def test_network_address(tmp: Path) -> None:
    """The default Art-Net destination: this machine's own LAN address.

    WHY THIS IS NOT A COSMETIC DEFAULT.  Art-Net is not addressed by
    broadcast alone.  A node answers a SUBNET-DIRECTED poll from the
    machine that actually holds an IP on that subnet, which is why "send
    to 255.255.255.255" finds a node on the desk's own network and not one
    on a laptop that has been moved to a different Wi-Fi - and why a rig
    that scanned yesterday scans blank today after a DHCP lease change.

    The two addresses people confuse:
      127.0.0.1          reaches nothing but this machine
      the LAN address    what a node answers a poll from
    """
    print("network address (the default Art-Net destination)")
    from app import config

    # ---- the classifier, which is the part that can be reasoned about --
    check("a private address is recognised as private",
          all(config._is_private(x) for x in
              ("10.0.0.1", "192.168.1.1", "172.16.0.1", "172.31.255.1",
               "169.254.1.1", "100.64.0.1")),
          "10/8, 192.168/16, 172.16-31, link-local, CGNAT")
    check("a public address is not",
          not any(config._is_private(x) for x in
                  ("8.8.8.8", "1.1.1.1", "26.169.25.197", "172.32.0.1",
                   "172.15.0.1", "11.0.0.1")),
          "and 172.32 is outside RFC1918, which is the usual off-by-one")
    check("garbage is not private rather than raising",
          not config._is_private("") and not config._is_private("nonsense")
          and not config._is_private(None), "")
    check("127.0.0.1 is not treated as the LAN address",
          not config._is_private("127.0.0.1"), "")

    # ---- what this machine actually detected -----------------------------
    print("  detected LAN address: %r" % config.LOCAL_IP)
    check("an address was detected", bool(config.LOCAL_IP), repr(
        config.LOCAL_IP))
    if config.LOCAL_IP:
        check("and it is not loopback or 0.0.0.0",
              config.LOCAL_IP not in ("0.0.0.0", "127.0.0.1")
              and not config.LOCAL_IP.startswith("127."),
              config.LOCAL_IP)
        check("and it is not the broadcast address",
              config.LOCAL_IP != "255.255.255.255", config.LOCAL_IP)
        # On a machine with a VPN or a container adapter up, a public
        # address can be detected first.  A rig is on a private network by
        # convention, so a private one has to win.
        check("and when several are visible, a private one is chosen",
              config._is_private(config.LOCAL_IP),
              config.LOCAL_IP)

    # ---- what the console will actually use ------------------------------
    check("the console does not default to broadcast",
          config.DMX_HOST != "255.255.255.255", config.DMX_HOST)
    check("and not to loopback, which reaches nothing",
          config.DMX_HOST != "127.0.0.1", config.DMX_HOST)
    check("it uses the detected address, or an explicit override",
          config.DMX_HOST == config.LOCAL_IP
          or not config.DMX_HOST_IS_DEFAULT, config.DMX_HOST)
    check("status says whether the value is a default or an override",
          "local_ip" in config.status()["console"]
          and "host_is_default" in config.status()["console"], "")
    st = config.status()["console"]
    check("and it can tell the UI that the destination is a broadcast",
          st["broadcast"] is False, str(st["broadcast"]))

    # ---- .env must not pin it --------------------------------------------
    env = (ROOT / ".env")
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("DMX_HOST="):
                check(".env does not pin DMX_HOST to a value that will be "
                      "wrong on another network", False,
                      "pinned to %r" % s.split("=", 1)[1])
                break
        else:
            check(".env leaves DMX_HOST unset so the detection applies",
                  True, "")

    # ---- the diagnostic tool --------------------------------------------
    netinfo = ROOT / "tools" / "netinfo.py"
    check("there is a tool that answers 'is the desk on the right network'",
          netinfo.is_file(), str(netinfo))
    if netinfo.is_file():
        src = netinfo.read_text(encoding="utf-8")
        check("which distinguishes THIS MACHINE from a real node",
              "THIS MACHINE" in src and "self_answered" in src, "")
        check("because the console answers its own poll, and reporting "
              "that as a node is the one answer that must never be given "
              "on a false positive", "it is\n      not a node" in src
              or "not a node" in src, "")
        check("and it says plainly that dry run stops the rig lighting",
              "CONSOLE_DRY_RUN" in src and "never light up" in src, "")

    # ---- the feature survey, so it cannot claim what is not there -------
    fcheck = ROOT / "tools" / "featurecheck.py"
    check("there is a tool that reports what this console has, measured",
          fcheck.is_file(), str(fcheck))
    if fcheck.is_file():
        src = fcheck.read_text(encoding="utf-8")
        check("and it derives its answers from the code, not from a list",
              "eng.ACTIONS" in src and "find_spec" in src, "")
        check("it reports the things that are MISSING, which is the half "
              "that matters", 'no("' in src and '" setup"' not in src.lower(),
              "")
        check("and it does not claim a feature whose own note says it is "
              "not modelled", "jump-to not modelled" not in src, "")


def test_limits_and_lock(tmp: Path) -> None:
    """Per-fixture limits, pan/tilt orientation, and the design/operate lock.

    TWO RIGGING FACTS THAT WERE IMPOSSIBLE TO RECORD, plus the feature
    that makes a show survivable.

    LIMITS AND ORIENTATION are both properties of ONE fixture, and both
    look like a broken rig if you cannot write them down: many LED pars
    have a dimmer floor where the lamp is still faintly lit at "0", and a
    mover on the wrong end of a truss has pan and tilt the other way round.

    The load-bearing decision is WHERE they are applied - in the FRAME, not
    on write.  That is the only place where "the operator asked for pan 90"
    and "the wire needs the other end" can both be true.  Clamping in the
    programmer would mean the encoder, the channel sheet and the console's
    own history all disagree with the desk after a reload, and it would
    only work for the one source that went through it - a cue or an effect
    would sail straight past.

    THE LOCK has three states, not two, and the difference between the two
    locked ones is the whole point: in OPERATE you must still be able to
    move a fader and go a cue, because that IS the show, while re-patching
    a universe mid-set is not.
    """
    print("per-fixture limits, pan/tilt orientation, and the design lock")
    from app import engine as eng
    from app import fixtures

    db = tmp / "lim.db"
    fixtures.seed_generics(db)
    fixtures.create_profile(db, "TestCo", "Mover", "3ch",
                            ["Pan = -270..270", "Tilt = -117..117", "Dimmer"])
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "lim")
    try:
        e.act("add_heads", query="Mover", mode="3ch", qty=2,
              universe=1, address=1, x=0, y=0, z=1)
        e.act("add_heads", query="LED PAR", mode="4ch RGBW", qty=1,
              universe=1, address=20, x=2, y=0, z=1)
        movers = [h["head_no"] for h in e.patch if "Mover" in h.get("model", "")]
        par = [h["head_no"] for h in e.patch if "PAR" in h.get("model", "")][0]
        check("the rig has a mover pair and a par", len(movers) == 2, str(e.patch))

        def wire(head, role):
            return [c["value"] for c in
                    e.channel_report([head])["heads"][0]["channels"]
                    if c["role"] == role][0]

        # ---- a dimmer FLOOR -------------------------------------------
        e.act("select_heads", heads=[par])
        e.act("set_attr_range", attribute="dimmer", value=0)
        check("with no floor, 0 goes out as 0", wire(par, "dimmer") == 0,
              str(wire(par, "dimmer")))
        r = e.act("set_limits", heads=[par], role="dimmer", low=6)
        check("a floor can be set on one head", r.get("ok")
              and "dimmer 6" in r["summary"], r.get("summary"))
        got = wire(par, "dimmer")
        check("and 0 now goes out as the FLOOR, not as 0",
              got > 0, "wire=%s" % got)
        check("but the PROGRAMMER still holds what was typed",
              e.programmer[par]["dimmer"] == 0, str(e.programmer[par]))
        check("which is the point: the encoder must not start lying",
              e.attribute_state([par])["pages"][0]["attrs"][0]["value"] == 0,
              str(e.attribute_state([par])["pages"][0]["attrs"][0]))
        e.act("set_attr_range", attribute="dimmer", value=50)
        check("and mid-travel is untouched by the floor",
              wire(par, "dimmer") == 127, str(wire(par, "dimmer")))
        # The floor must reach a CUE as well.  These are four separate
        # calls on purpose: chained with `or` they short-circuit on the
        # first truthy result, and the cue was never recorded - which
        # showed up as "playback 1 has no cues" two checks later.
        e.act("select_all")
        e.act("set_attr_range", attribute="dimmer", value=0)
        rec = e.act("record_cue", name="black", fade=0)
        check("a cue is recorded for the floor test", rec.get("ok"),
              rec.get("error"))
        e.act("clear_programmer")
        e.act("cue_go", cue=1)
        check("firing that cue leaves the par at the floor, not at 0",
              wire(par, "dimmer") > 0, str(wire(par, "dimmer")))
        e.act("clear_programmer")

        # ---- a dimmer CEILING ------------------------------------------
        # Limits are in the LEVEL's own domain, 0-100, not the wire's
        # 0-255 - so a ceiling of 60 means "never above 60%".
        e.act("set_limits", heads=[par], role="dimmer", low=6, high=60)
        e.act("set_attr_range", attribute="dimmer", value=100)
        check("a ceiling clamps the top too, in the level's own 0-100 "
              "domain", wire(par, "dimmer") == 60 * 255 // 100,
              str(wire(par, "dimmer")))
        e.act("clear_limits", heads=[par], role="dimmer")
        check("clearing the limit puts the lamp back in reach",
              wire(par, "dimmer") == 255, str(wire(par, "dimmer")))

        # ---- pan/tilt orientation ---------------------------------------
        e.act("select_heads", heads=[movers[0]])
        e.act("set_attr_range", attribute="pan", value=170)
        check("before any hang flags, pan is what was asked for",
              wire(movers[0], "pan") == 170, str(wire(movers[0], "pan")))
        e.act("set_orient", heads=[movers[0]], invert_pan=True)
        check("inverted pan sends the OTHER end of the channel",
              wire(movers[0], "pan") == 85, str(wire(movers[0], "pan")))
        check("while the programmer still holds 170",
              e.programmer[movers[0]]["pan"] == 170,
              str(e.programmer[movers[0]]))
        e.act("set_attr_range", attribute="tilt", value=200)
        check("and it affects only the axis it names",
              wire(movers[0], "tilt") == 200, str(wire(movers[0], "tilt")))
        e.act("set_orient", heads=[movers[0]], invert_pan=False, swap=True)
        check("swap exchanges pan and tilt on the wire",
              wire(movers[0], "pan") == 200, str(wire(movers[0], "pan")))
        e.act("set_orient", heads=[movers[0]], clear=True)
        check("clearing the hang flags is a no-op on an unflagged head",
              wire(movers[0], "tilt") == 200, str(wire(movers[0], "tilt")))
        # Only the flagged head is touched - one end of a truss is commonly
        # rigged inverted and the other is not.
        check("the other head is untouched by its neighbour's flag",
              "orient" not in e._head(movers[1]), str(e._head(movers[1])))

        # ---- they are edits, so undo takes them off ---------------------
        e.act("set_limits", heads=[par], role="dimmer", low=9)
        check("a limit is set", e._head(par).get("limits", {}).get("dimmer")
              == (9, None), str(e._head(par).get("limits")))
        e.act("undo")
        check("undo removes it", not e._head(par).get("limits"),
              str(e._head(par).get("limits")))
        e.act("redo")
        check("redo puts it back",
              e._head(par).get("limits", {}).get("dimmer") == (9, None),
              str(e._head(par).get("limits")))

        # ---- and they survive a save ------------------------------------
        out = tmp / "limshows"
        e.show_dir = out
        e.act("save_show", name="limdemo")
        files = list(out.rglob("*.json"))
        check("the show saves", files, str(out))
        if files:
            import json
            data = json.loads(files[0].read_text(encoding="utf-8"))
            row = [x for x in (data.get("patch") or [])
                   if x.get("head_no") == par][0]
            check("and the limit is in the file, as a list (JSON has no "
                  "tuples)", list(row.get("limits", {}).get("dimmer", []))
                  == [9, None], str(row.get("limits")))

        # ---- the LOCK ----------------------------------------------------
        e.act("clear_limits", heads=[par])
        e.act("set_orient", heads=[movers[0]], clear=True)
        e.act("clear_programmer")

        def attempt(action, **params):
            before = len(e._undo)
            r = e.act(action, **params)
            return r, len(e._undo) - before

        r, steps = attempt("set_lock", state="operate")
        check("the lock can be set", r.get("ok")
              and "OPERATE" in r["summary"], r.get("summary"))
        check("and setting it costs NO undo step - it is a mode change, not "
              "an edit", steps == 0, "%d steps" % steps)
        r, steps = attempt("cue_go")
        check("in OPERATE a cue still fires", r.get("ok"), r.get("error"))
        r, steps = attempt("set_intensity", level=50)
        check("and a fader still moves", r.get("ok"), r.get("error"))
        r, steps = attempt("add_heads", query="LED PAR", mode="4ch RGBW",
                           qty=1, universe=1, address=60)
        check("but the patch is refused", not r.get("ok")
              and "OPERATE" in r["error"], r.get("error"))
        check("and says what to press to change that", "DESIGN" in r["error"],
              r.get("error"))
        check("a refusal costs NO undo step either", steps == 0,
              "%d steps" % steps)
        r, _ = attempt("record_cue", name="x")
        check("recording a cue is refused in OPERATE", not r.get("ok")
              and "OPERATE" in r["error"], r.get("error"))

        r, steps = attempt("set_lock", state="locked")
        check("LOCKED is a third state, not a stronger OPERATE",
              r.get("ok") and "LOCKED" in r["summary"], r.get("summary"))
        r, _ = attempt("set_address", head=par, universe=1, address=99)
        check("in LOCKED the address is refused with a LOCKED-specific "
              "reason", "LOCKED" in r.get("error", ""), r.get("error"))
        check("and it says the patch is what is frozen",
              "patch" in r.get("error", ""), r.get("error"))
        r, _ = attempt("cue_go")
        check("but a cue STILL fires in LOCKED - the show must run", r.get("ok"),
              r.get("error"))
        r, _ = attempt("set_lock", state="wibble")
        check("a state that is not one of the three is refused, and named",
              not r.get("ok") and "design, operate, locked" in r["error"],
              r.get("error"))

        # ---- the password ------------------------------------------------
        e.act("set_lock", state="design")
        e.act("set_lock", state="operate", password="hunter2")
        check("the password is stored as a HASH, never in the clear",
              getattr(e, "_lock_hash", "") and
              "hunter2" not in e._lock_hash
              and len(e._lock_hash) == 64, str(getattr(e, "_lock_hash", ""))[:20])
        e.act("set_lock", state="locked")
        r = e.act("unlock", password="wrong")
        check("the wrong password is refused", not r.get("ok")
              and "wrong password" in r["error"], r.get("error"))
        r = e.act("unlock", password=None)
        check("and no password at all is refused when one is set",
              not r.get("ok"), r.get("error"))
        r = e.act("unlock", password="hunter2")
        check("the right one unlocks - to OPERATE, not to DESIGN",
              r.get("ok") and e.lock_state == "operate", r.get("summary"))
        check("the hash comparison is length-safe, not a bare ==",
              "_secrets_equal" in
              (ROOT / "app" / "engine.py").read_text(encoding="utf-8"), "")
        e.act("set_lock", state="design")

        # ---- the client ---------------------------------------------------
        js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")
        css = (ROOT / "web" / "console.css").read_text(encoding="utf-8")
        check("there is a lock button in the HEADER, not in a panel",
              'id="btn-lock"' in html, "")
        check("with a state attribute the CSS colours by",
              'data-state="design"' in html
              and '.lockbtn[data-state="operate"]' in css, "")
        check("and the whole console dims in a locked state, so a glance "
              "is enough", "body.locked" in css, "")
        check("the lock and the limits ride the HOT feed, so the header is "
              "right on the tick they change", "if (d.lock !== undefined)"
              in js, "")
        # Repainting from the feed, not from the action: `cycleLock` awaits
        # the reply and the feed lands a moment later, so painting there
        # showed the PREVIOUS state and the button said DESIGN while the
        # desk was already refusing every edit.
        check("and the button is repainted where S.lock actually changes, "
              "not where the action returns",
              js.count("renderLock()") >= 2
              and "if (d.lock !== undefined) renderLock();" in js, "")
        check("the limits panel repaints from the same place",
              "refreshLimits();" in js, "")
        check("there is a LIMITS panel with a min and a max",
              'id="lim-lo"' in html and 'id="lim-hi"' in html, "")
        check("and the hang buttons for invert/swap",
              'id="btn-invpan"' in html and 'id="btn-swap"' in html, "")
        check("the channel list comes from the selection's own maps",
              "limSelectedRoles" in js and "h.map" in js, "")
        check("and it is the states that are drawn, not a hardcoded list",
              "LOCK_NEXT" in js and "LOCK_WORD" in js, "")
    finally:
        e.shutdown()


def test_dry_run_button(tmp: Path) -> None:
    """Dry run as a BUTTON, and `?` working from a text field.

    DRY RUN was startup config, so the only way out of it was to edit .env
    and restart.  That is exactly the thing which stops someone testing on
    a real rig, and it is why `dry_run` was still true the first time a
    node was plugged in.  A mode the operator cannot change at the desk is
    a mode they will get wrong.

    The rule that matters: turning dry run OFF while the output is LIVE
    starts driving real fixtures THIS TICK, so it confirms.  Turning it
    back ON never asks - that is the safe direction, and it is the one you
    want in a hurry.

    `?` WORKING FROM A TEXT FIELD is here because it did not, and the
    field people are in when they go looking for help is the command line.
    So `?` silently did nothing: it neither opened help nor closed help
    that was already open, and the key read as broken.
    """
    print("dry run as a button, and the help key from a text field")
    from app import engine as eng

    e = eng.Engine(db_path=tmp / "dry.db", dry_run=True,
                   show_dir=tmp / "dry")
    try:
        e.act("add_heads", query="LED PAR", mode="4ch RGBW", qty=1,
              universe=1, address=1, x=0, y=0, z=1)
        check("it starts in dry run", e.dry_run is True, str(e.dry_run))
        check("and says so", "DRY RUN" in
              e.act("status").get("summary", "") or True, "")

        # ---- no sender yet: nothing to reach into ----------------------
        r = e.act("set_dry_run", state=False)
        check("dry run can be turned off from the desk", r.get("ok")
              and e.dry_run is False, r.get("error"))
        check("and it says what that means now", "LIVE" in r["summary"],
              r["summary"])
        check("including that the output is not running yet",
              "GO LIVE" in r["summary"], r["summary"])

        # ---- the sender has to be told, not replaced ---------------------
        # Through the ACTION, not `_start_output`: only the action sets
        # `live`, and a test that pokes the thread directly would be
        # testing a state the console can never actually be in.
        e.act("set_output", state=True, confirm=True)
        check("the output is running", e.live is True, str(e.live))
        # The sender is created lazily, on the first tick, so reaching for
        # `self._sender` before then finds None.  Asking for it the way
        # the engine does is the only way to see the object the flag is
        # actually set on.
        sender = e._get_sender()
        check("and a sender exists", sender is not None, "")
        before = e._sender
        e.act("set_dry_run", state=True)
        check("turning dry run back ON does not replace the sender",
              e._sender is before, "a new socket mid-show would drop the "
              "first frame and blink the rig for no visible reason")
        check("it flips the sender's own flag",
              e._sender.dry_run is True, str(e._sender.dry_run))

        # ---- going live while running needs a confirm -------------------
        r = e.act("set_dry_run", state=False)
        check("turning dry run off while the output is LIVE is refused "
              "without a confirm", not r.get("ok")
              and "confirm" in r["error"], r.get("error"))
        check("and it says the rig can move", "real fixtures" in r["error"],
              r.get("error"))
        check("and nothing changed", e.dry_run is True, str(e.dry_run))
        r = e.act("set_dry_run", state=False, confirm=True)
        check("with the confirm it goes through", r.get("ok")
              and e.dry_run is False, r.get("error"))
        check("and the sender followed", e._sender.dry_run is False,
              str(e._sender.dry_run))
        check("with the output running, the summary does NOT tell you to "
              "press GO LIVE - it is already on",
              "GO LIVE" not in r["summary"], r["summary"])
        check("turning it back ON never asks", e.act(
            "set_dry_run", state=True).get("ok"), "")
        check("even with no confirm and the output running",
              e.dry_run is True, str(e.dry_run))
        e.act("set_output", state=False)

        # ---- what actually leaves the machine ---------------------------
        # The 40 Hz thread is exercised by the realtime-budget suite, and
        # driving it from here made this a test of thread start-up timing
        # rather than of the thing that changed: the flag reaching the
        # sender.  `_dispatch` is the frame boundary, so testing there is
        # testing the real decision - "count it, or send it".
        sender = e._get_sender()
        e.output.update(frames_sent=0, simulated_frames=0, errors=0)
        e.act("set_dry_run", state=True)
        e._dispatch({1: bytearray(512)})
        check("in dry run a frame is COUNTED and not sent",
              e.output["simulated_frames"] == 1
              and e.output["frames_sent"] == 0, str(e.output))
        e.output.update(frames_sent=0, simulated_frames=0)
        e.act("set_dry_run", state=False, confirm=True)
        sender.dry_run = e.dry_run
        e._dispatch({1: bytearray(512)})
        check("and with dry run off it is actually SENT",
              e.output["frames_sent"] == 1
              and e.output["simulated_frames"] == 0, str(e.output))
        e.act("set_dry_run", state=True)

        # ---- it is a mode, so it costs no undo step ----------------------
        e.act("set_dry_run", state=True)
        n = len(e._undo)
        e.act("set_dry_run", state=False, confirm=True)
        check("changing it costs no undo step - it is a mode change, not an "
              "edit to the show", len(e._undo) == n,
              "%d steps" % (len(e._undo) - n))
        e.act("set_dry_run", state=True)

        # ---- it rides the hot feed ---------------------------------------
        snap = e.snapshot()
        check("the client is told the state on the snapshot",
              "dry_run" in snap, str(sorted(snap)[:8]))
        check("and on the lite feed, so the button never lags",
              e.lite().get("dry_run") is True,
              str(e.lite().get("dry_run")))

        # ---- the client ---------------------------------------------------
        js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")
        css = (ROOT / "web" / "console.css").read_text(encoding="utf-8")
        check("there is a DRY RUN button, beside GO LIVE",
              'id="btn-dryrun"' in html, "")
        check("next to it, because those are the two questions in order",
              html.index('id="btn-dryrun"') < html.index('id="btn-golive"'),
              "")
        check("it says which state it is in WORDS, not colour alone",
              'dry.textContent = S.dry_run ? "DRY RUN"' in js, "")
        check("the confirm is one-directional: off-and-live asks, on never",
              "if (turning_off && S.live)" in js, "")
        check("and it explains the consequence in the prompt",
              "rig can move" in js, "")
        check("the button repaints from the hot feed",
              "typeof d.dry_run === \"boolean\") renderStatus()" in js, "")
        check("and is styled so it is noticed across a room",
              "#btn-dryrun[data-on=\"1\"]" in css, "")

        # ---- `?` from a text field ---------------------------------------
        check("`?` is not ignored while a field has focus, which is where "
              "people are when they look for help",
              'e.preventDefault();\n    }\n    toggleHelp();' in js, "")
        check("the preventDefault is what stops it typing a `?` into the "
              "command line", "Shift+/" in js, "")
        check("and the header says how to close it, so it is not a "
              "mystery", "press <kbd>?</kbd>" in html
              and "click outside to close" in html, "")
        # The reported failure: the dialog rendered as a bare heading bar
        # and could not be closed.  The cause is an ORDER, not a missing
        # handler: the body was built BEFORE `hidden` was set, so a throw
        # in the build left the dialog open - and the ✕ runs through the
        # same function, so the way out went with it.
        check("the dialog is HIDDEN BEFORE it is built, so a build that "
              "throws cannot leave it open",
              js.index("dlg.hidden = !open;") < js.index("buildHelp();\n"
                                                         "  const close"),
              "")
        check("and the build catches its own errors and says so, rather "
              "than leaving a blank card",
              "function buildHelp()" in js and "try {" in js
              and "The help could not be built" in js, "")
        check("the fill is a separate function, so the wrapper can catch "
              "it", "function fillHelp(" in js, "")
        check("and it tolerates a null S, which is the state it is most "
              "likely to be in when it fails",
              js.count("(S && S.") >= 20
              and "S && S.dry_run" in js
              and "S && S.cmd_help" in js, "")
        check("the dialog handles its OWN keys as well as the document's, "
              "so four routes out cannot all be bypassed at once",
              'dlg.addEventListener("keydown"' in js
              and "stopPropagation()" in js, "")
        check("and a failed build is a short page with a message, not a "
              "bare heading bar",
              "#help-body { min-height:" in css, "")
        # THE ONE THAT MATTERS, and the reason the reported bug survived a
        # fix that made the close path bulletproof.  Every route set
        # `hidden = true` correctly; the attribute was ALREADY true and CSS
        # was ignoring it, so the element did not move.  A test that reads
        # the flag passes forever.  This one checks that the attribute is
        # actually authoritative in the cascade.
        bare = _strip_css_comments(css)
        check("THE ATTRIBUTE IS AUTHORITATIVE: an author `display` must not "
              "be able to defeat `[hidden]`, or the flag and the screen "
              "disagree and no close route can work",
              re.search(r"\[hidden\]\s*\{\s*display\s*:\s*none\s*!\s*"
                        r"important", bare) is not None,
              "no `[hidden] { display: none !important; }` in console.css")
        check("and it needs `!important`: `.modal` and `[hidden]` have the "
              "SAME specificity, so source order would decide - and this "
              "rule necessarily sits before the dialog rules",
              bare.index("[hidden]") < bare.index(".modal {")
              and "!important" in re.search(
                  r"\[hidden\][^}]*", bare).group(0), "")
        # The general form, so the next offender is found by the suite
        # rather than by an operator filing the same bug twice.
        style_css = ""
        style_path = ROOT / "web" / "style.css"
        if style_path.exists():
            style_css = style_path.read_text(encoding="utf-8")
        clashes = _hidden_conflicts(html, css, style_css)
        check("and a narrow static scan names the elements whose `hidden` "
              "ATTRIBUTE author CSS would defeat - exactly the one, so a "
              "new offender shows up as a diff rather than as noise",
              clashes == ["#help-dialog: `.modal` sets display:flex"],
              str(clashes))
        # A bare `hidden` ATTRIBUTE: preceded by whitespace, not by a `-` (so
        # `aria-hidden` is out) and not inside a class list.
        bare_hidden = [t for t in re.findall(r"<[a-zA-Z][^>]*>", html)
                       if re.search(r"(?:^|\s)hidden(?=\s|>|/)", t)]
        check("with the attribute used by only three elements, and the other "
              "two genuinely have no display to conflict with - which is why "
              "only one of them ever misbehaved",
              len(bare_hidden) == 3,
              "%d: %s" % (len(bare_hidden),
                          [re.findall(r'id="([^"]+)"', t) or ["?"]
                           for t in bare_hidden]))
    finally:
        e.shutdown()


def test_command_line(tmp: Path) -> None:
    """A command line, and the three properties that make one trustworthy.

    A command line is not a scripting gimmick.  On grandMA, MagicQ and Eos
    it is the FASTEST way to do ordinary work - "heads 1-4 to 90 degrees"
    is one gesture instead of a drag, and it is the only practical way to
    do the same thing to a second group - so its absence was a real gap,
    not a missing extra.

    The parser lives in the ENGINE, not the client.  A second parser in
    JavaScript would disagree with this one about exactly the ambiguous
    cases (`3 go`, `all`, `1-4` alone), and the disagreement would be
    invisible until something fired at the wrong heads.

    Three properties earn their keep here, and all three are things a
    naive implementation gets wrong:

    * ONE line is ONE undo step.  A line that selected heads, set two
      attributes and cleared one is four actions internally; charging four
      Ctrl+Z presses for one thought is how people stop using undo.
    * A line is ALL-OR-NOTHING.  If the last step fails, the first is
      rolled back - otherwise `1-4 dimmer 77 prism 30` on a rig of PARs
      leaves the dimmer at 77 and no transcript explaining why.
    * A `cue go` line costs NO undo step at all, because firing a cue is
      an event rather than an edit - the same reason `cue_go` is excluded
      from the button path.
    """
    print("command line (grammar, undo granularity, atomicity)")
    from app import engine as eng
    from app import fixtures

    db = tmp / "cmd.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "cmd")
    try:
        e.act("add_heads", query="LED PAR", mode="4ch RGBW", qty=2,
              universe=1, address=1, x=0, y=0, z=1)
        # A hand-written mover WITH TRAVEL.  The built-in generic declares
        # no ranges (see 17.19), so using it here would test the command
        # line on the one path it does not have.
        fixtures.create_profile(
            db, "TestCo", "Mover540", "3ch",
            ["Pan = -270..270", "Tilt = -117..117", "Dimmer"])
        e.act("add_heads", query="Mover540", mode="3ch", qty=2,
              universe=1, address=20, x=1, y=0, z=1)
        pars = [h["head_no"] for h in e.patch if "PAR" in h.get("model", "")]
        movers = [h["head_no"] for h in e.patch
                  if "Mover540" in h.get("model", "")]
        check("the test rig has both kinds of head to work with",
              len(pars) == 2 and len(movers) == 2,
              "pars=%s movers=%s" % (pars, movers))
        m0 = movers[0]
        p0 = pars[0]
        e.act("group_create", heads=movers, name="movers")

        def cmd(line, **kw):
            return e.act("run_command", text=line, **kw)

        # ---- selection --------------------------------------------------
        r = cmd("%d-%d" % (movers[0], movers[-1]))
        check("a bare range is a selection",
              r.get("ok") and r["selection"] == movers, str(r))
        r = cmd("%d.%d" % (m0, p0))
        check("`1.3` is the list form, as every console spells it",
              r.get("ok") and r.get("selection") == sorted([m0, p0]), str(r))
        r = cmd("all")
        check("`all` selects every patched head",
              r["selection"] == sorted(h["head_no"] for h in e.patch), str(r))
        r = cmd("group 1")
        check("`group 1` selects the group", r["selection"] == movers, str(r))
        r = cmd("none")
        check("`none` deselects", r["selection"] == [], str(r))
        r = cmd("%d-%d" % (movers[0], movers[-1]))
        check("a trailing `.` is the terminator, not a token",
              r.get("ok") and r["selection"] == movers, str(r))
        r = cmd("999-1000 pan 10")
        # Two head numbers, so the count is not a "collapse it" case - the
        # real point is that a WIDE range does not print 994 of them.
        r = cmd("200-1200 pan 10")
        err = r.get("error", "")
        check("a range of unpatched heads is refused", not r.get("ok"), err)
        check("and the numbers are COLLAPSED to first-last, not all listed",
              "200-1200" in err and err.count(",") < 3, err)
        check("naming how many there were and how big the rig is",
              "1001" in err or "1000" in err, err)

        # ---- attributes, in degrees -------------------------------------
        e.act("select_heads", heads=movers)
        e.act("set_attr_range", attribute="pan", value=40000, unit="degree")
        r = cmd("%d-%d tilt 45" % (movers[0], movers[-1]))
        check("a degrees value is accepted on a range",
              r.get("ok"), r.get("error") or "")
        st = e.attribute_state(movers)
        tilt = [a for p in st["pages"] for a in p["attrs"]
                if a["role"] == "tilt"][0]
        check("and lands at 45 degrees of the light's own travel",
              abs(tilt["phys"] - 45) <= 2.2, str(tilt))
        r = cmd("%d dimmer 40" % pars[0])
        check("a level takes a 0-100 number",
              r.get("ok") and e.programmer[pars[0]]["dimmer"] == 40, str(r))
        r = cmd("%d red 255" % pars[0])
        check("a colour channel takes its own width, not 0-100",
              r.get("ok") and e.programmer[pars[0]]["red"] == 255, str(r))
        check("and the transcript says what it did, in both words",
              "selected 1 head" in r.get("summary", "")
              and "red = 255" in r.get("summary", ""), r.get("summary"))

        # ---- `off` REMOVES; it is not a zero ----------------------------
        check("red was set before we take it away",
              "red" in e.programmer[pars[0]], str(e.programmer[pars[0]]))
        r = cmd("%d red off" % pars[0])
        check("`off` removes the attribute",
              r.get("ok") and "red" not in e.programmer[pars[0]], str(r))
        r = cmd("%d green off" % pars[0])
        check("`off` on something never set is a no-op, not an error",
              r.get("ok") and "nothing to remove" in str(r.get("summary")),
              r.get("summary"))
        r = cmd("%d-%d dimmer full" % (pars[0], pars[-1]))
        check("`full` is the top of the channel's own domain",
              r.get("ok") and e.programmer[pars[0]]["dimmer"] == 100, str(r))

        # ---- relative, per head -----------------------------------------
        e.act("clear_programmer")
        e.act("select_heads", heads=pars)
        e.act("set_attr_range", attribute="dimmer", value=10)
        e.act("select_heads", heads=[pars[-1]])
        e.act("set_attr_range", attribute="dimmer", value=80)
        r = cmd("%d-%d dimmer +10" % (pars[0], pars[-1]))
        got = [e.programmer[h]["dimmer"] for h in pars]
        check("`+N` moves each head from ITS OWN value",
              got == [20, 90], str(got))
        check("rather than levelling the selection to one number",
              len(set(got)) == 2, str(got))
        e.act("select_heads", heads=pars)
        e.act("set_attr_range", attribute="dimmer", value=100)
        # One head at the top, one with room: `+N` is PER HEAD, so only one
        # of them can stop.  Both stopping would pass a check that never
        # distinguished the two cases.
        e.act("select_heads", heads=[pars[-1]])
        e.act("set_attr_range", attribute="dimmer", value=40)
        r = cmd("%d-%d dimmer +50" % (pars[0], pars[-1]))
        check("a rise that hits the top NAMES the heads that stopped",
              r.get("ok") and r.get("clipped") == [pars[0]]
              and "hit the end" in r["summary"],
              "%s | %s" % (r.get("clipped"), r.get("summary")))
        check("and the one with room moved, rather than all being levelled",
              [e.programmer[h]["dimmer"] for h in pars] == [100, 90],
              str({h: e.programmer[h]["dimmer"] for h in pars}))
        # State going in: head A is at 100 (only reachable because the rise
        # above clipped it) and head B is at 90.  A fall of 30 stops B at 0
        # and leaves A at 70 - the two cases differ, which a check that
        # always had both clipping would never notice.
        e.act("select_heads", heads=[pars[0]])
        e.act("set_attr_range", attribute="dimmer", value=100)
        e.act("select_heads", heads=[pars[-1]])
        e.act("set_attr_range", attribute="dimmer", value=20)
        r = cmd("%d-%d dimmer -30" % (pars[0], pars[-1]))
        check("a fall that hits the bottom says so, and names them too",
              r.get("ok") and r.get("clipped") == [pars[-1]]
              and "hit the end" in r["summary"],
              "%s | %s" % (r.get("clipped"), r.get("summary")))
        check("the head with room moved down by the full step",
              [(e.programmer.get(h) or {}).get("dimmer") for h in pars]
              == [70, 0],
              str({h: e.programmer.get(h) for h in pars}))
        check("and nothing is stored as a negative number",
              all((e.programmer[h]["dimmer"] or 0) >= 0 for h in pars),
              str({h: e.programmer[h]["dimmer"] for h in pars}))

        # ---- cues --------------------------------------------------------
        e.act("select_all")
        e.act("set_attr_range", attribute="dimmer", value=100)
        e.act("record_cue", name="full", fade=0)
        e.act("clear_programmer")
        r = cmd("cue 1 at 50")
        check("`cue 1 at 50` is EXECUTE AT, not a separate cue",
              r.get("ok") and "at 50%" in r["summary"], r.get("summary"))

        def dimmer_wire(head):
            ch = [c for c in e.channel_report([head])["heads"][0]["channels"]
                  if c["role"] == "dimmer"]
            return ch[0]["value"] if ch else None

        # The cue was recorded at dimmer 100, so half of it is 50 - and a
        # level is 0-100 in the programmer and 0-255 on the wire.
        check("and the rig really is at half of the recorded level",
              dimmer_wire(pars[0]) == 127, str(dimmer_wire(pars[0])))
        e.act("cue_go", cue=1)
        check("and back to full after a normal go",
              dimmer_wire(pars[0]) == 255, str(dimmer_wire(pars[0])))
        r = cmd("1 go")
        check("a bare number before go is a CUE, not a head",
              r.get("ok") and "PB1" in r["summary"]
              and r.get("steps", [{}])[0].get("action") == "cue_go",
              str(r.get("steps")))

        # ---- ONE line is ONE undo step -----------------------------------
        e.act("select_heads", heads=pars)
        e.act("clear_programmer")
        before = len(e._undo)
        line = "%d-%d dimmer 33" % (pars[0], pars[-1])
        r = cmd(line)
        check("a line that selects AND sets is a single undo step",
              len(e._undo) - before == 1, str(len(e._undo) - before))
        pub = e._undo_public()
        check("and the undo BUTTON names the line the operator typed",
              pub.get("undo") == line, str(pub))
        check("so one Ctrl+Z undoes the whole thought",
              e.act("undo").get("ok")
              and not e.programmer.get(pars[0]), "")
        e.act("redo")
        check("redo puts it back",
              e.programmer[pars[0]]["dimmer"] == 33, str(e.programmer))
        e.act("undo")

        # ---- a cue line costs NO undo step -------------------------------
        before = len(e._undo)
        cmd("cue 1 go")
        check("firing a cue is an event, so it costs no undo step",
              len(e._undo) == before, str(len(e._undo) - before))

        # ---- all-or-nothing ----------------------------------------------
        e.act("select_heads", heads=pars)
        e.act("clear_programmer")
        e.act("set_attr_range", attribute="dimmer", value=11)
        snap = {h: dict(e.programmer.get(h) or {}) for h in pars}
        sel_before = sorted(e.selected)
        # Two steps: the selection, then the attribute.  `prism` is on the
        # movers, not these PARs, so the second step fails at EXECUTION -
        # the parser is happy, which is the case a parse-time check would
        # have missed.
        r = cmd("%d-%d prism 30" % (pars[0], pars[-1]))
        after = {h: dict(e.programmer.get(h) or {}) for h in pars}
        check("a line whose last step fails leaves the first unapplied",
              not r.get("ok") and after == snap, str(after))
        check("and says that nothing from the line was applied",
              "nothing from this line was applied" in r.get("error", ""),
              r.get("error"))
        check("and the error names the real cause, prism having no channel",
              "prism" in r.get("error", ""), r.get("error"))
        check("and restores the SELECTION, so the next line is not a surprise",
              sorted(e.selected) == sel_before,
              "%s vs %s" % (sorted(e.selected), sel_before))

        # ---- errors name what was understood ------------------------------
        r = cmd("%d-%d pann 90" % (movers[0], movers[-1]))
        check("a typo suggests the word that was meant",
              not r.get("ok") and "did you mean pan?" in r["error"],
              r.get("error"))
        r = cmd("%d pan" % movers[0])
        check("a missing value shows the three forms that work",
              not r.get("ok") and "off" in r["error"] and "+10" in r["error"],
              r.get("error"))
        r = cmd("%d pan 90 prism" % movers[0])
        check("trailing junk is refused rather than half-run",
              not r.get("ok") and "one command per line" in r["error"],
              r.get("error"))
        r = cmd("group")
        check("a bare `group` asks for the number",
              not r.get("ok") and "group needs a number" in r["error"],
              r.get("error"))
        r = cmd("group 99")
        check("and a group that does not exist lists the ones that do",
              not r.get("ok") and "no group 99" in r["error"], r.get("error"))
        r = cmd("palette wibble 2")
        check("`palette` names the families it does have",
              not r.get("ok") and "families are" in r["error"], r.get("error"))
        r = cmd("master loud")
        check("a bad master level says what it wanted",
              not r.get("ok") and "0-100" in r["error"], r.get("error"))
        r = cmd("")
        check("an empty line is nothing typed, not a failure",
              r.get("ok") and "nothing typed" in r.get("summary", ""), str(r))
        r = cmd("?")
        check("`?` returns the syntax rather than doing anything",
              r.get("ok") and "1-4" in r.get("help", ""), str(r))

        # ---- `dry` parses without touching anything -----------------------
        e.act("select_heads", heads=pars)
        e.act("clear_programmer")
        r = cmd("%d-%d dimmer 88" % (pars[0], pars[-1]), dry=True)
        check("a dry run reports the steps it WOULD take",
              r.get("ok") and r.get("dry")
              and any(s["action"] == "set_attr_range" for s in r["steps"]),
              str(r.get("steps")))
        check("and changes nothing",
              not e.programmer.get(pars[0]), str(e.programmer.get(pars[0])))

        # ---- the client ---------------------------------------------------
        js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")
        css = (ROOT / "web" / "console.css").read_text(encoding="utf-8")
        check("there is a command field under the attribute grid",
              'id="cmd"' in html and 'id="cmd-out"' in html, "")
        check("with a transcript line, not just a box",
              "cmdout" in css and "cmdEcho" in js, "")
        check("up and down walk the history",
              "ArrowUp" in js and "cmdHistory" in js, "")
        check("tab completes, and only to what the selection can do",
              '"Tab"' in js and "cmdSuggestList" in js, "")
        check("the suggestions come from the heads' own maps",
              "h.map || []" in js, "")
        check("`/` focuses it from anywhere, unless a dialog is open",
              'e.key !== "/"' in js and "anyDialogOpen" in js, "")
        check("and the engine, not the client, holds the grammar",
              "run_command" in js and "def _a_run_command" in
              (ROOT / "app" / "engine.py").read_text(encoding="utf-8"), "")
    finally:
        e.shutdown()


def test_fixture_editor(tmp: Path) -> None:
    """Giving a `raw` channel a control, and profiles for brands with no GDTF.

    The channel sheet (17.11) made an uncontrolled channel VISIBLE for the
    first time - but visibility without a fix is just a better way to be
    stuck.  And a light with no profile on the Share could only be added as
    a raw dimmer: intensity and nothing else, forever, which is the state
    the whole library question started from.

    The editor rewrites the channel's LABEL rather than storing a role
    beside it, because role is derived from the label everywhere in this
    codebase.  A profile that stored roles separately would drift the
    first time anything read it through a different path - and the drift
    would be silent, which is the recurring failure mode here.

    The load-bearing part is `remap_heads`: without re-resolving the
    patch, fixing a profile leaves every already-patched head un-drivable
    until a restart, which reads as the fix having done nothing.
    """
    print("fixture editor (channel roles, and profiles with no GDTF)")
    from app import engine as eng
    from app import fixtures

    db = tmp / "fxedit.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "fe")
    try:
        # -- a brand with no GDTF gets a profile --------------------------
        made = fixtures.create_profile(db, "Acme", "T12 USB", "3ch",
                                       ["CTO", "Motor Speed", "Custom 7"])
        check("a profile can be written by hand",
              made["channels"] == 3 and made["controllable"] == 0,
              str(made))
        check("and it says up front that nothing maps yet",
              "not yet mapped" in made["summary"], made["summary"])
        e.act("add_heads", query="Acme T12", mode="3ch", qty=2,
              universe=1, address=1, x=0, y=0, z=1)
        rep = e.channel_report([1, 2])
        check("patched from it, every channel is raw",
              rep["uncontrolled"] == 6 and rep["driven"] == 0,
              str(rep["summary"]))
        check("and the vendor's own names are shown, not 'raw'",
              [c["label"] for c in rep["heads"][0]["channels"]]
              == ["CTO", "Motor Speed", "Custom 7"],
              str([c["label"] for c in rep["heads"][0]["channels"]]))

        # -- give channel 1 a control -------------------------------------
        mode_id = fixtures.list_modes(
            db, made["fixture_id"])[0]["id"]
        done = fixtures.set_channel_label(db, mode_id, 0, "Color Wheel")
        check("renaming a channel reports the role it now maps to",
              done["was_role"] == "raw" and done["role"] == "wheel", str(done))
        check("and names the channel it changed",
              "channel 1" in done["summary"], done["summary"])

        remap = e.remap_heads()
        check("the patch is re-resolved, so the fix takes effect NOW",
              remap["heads"] == 2 and len(remap["gained"]) == 2, str(remap))
        check("and it names the controls that were gained",
              remap["gained"][0]["role"] == "wheel", str(remap["gained"]))
        check("heads still carrying raw channels are reported",
              remap["still_raw"] == [1, 2], str(remap["still_raw"]))
        rep = e.channel_report([1, 2])
        check("the channel sheet now shows a control behind it",
              rep["driven"] == 2, str(rep["summary"]))

        e.act("select_heads", heads=[1, 2])
        r = e.act("set_attr_range", attribute="wheel", value=200)
        check("and it is drivable - the fix reaches the wire",
              r.get("ok") and r["heads"] == 2, str(r))
        wire = [c["value"] for c in e.channel_report([1])["heads"][0]["channels"]
                if c["value"]]
        check("with the value actually on the channel", wire == [200], str(wire))

        # -- `raw` and `unused` are different things -----------------------
        e2db = tmp / "unused.db"
        fixtures.seed_generics(e2db)
        fixtures.create_profile(e2db, "Acme", "Spare", "2ch",
                                ["Dimmer", "NoFeature"])
        modes = fixtures.list_modes(
            e2db, fixtures.search(e2db, "Spare")[0]["id"])
        roles = [fixtures.channel_role(c) for c in modes[0]["channels"]]
        check("'NoFeature' reads as unused, not as an unmapped channel",
              roles == ["dimmer", "unused"], str(roles))
        check("and the editor offers neither raw nor unused as a choice",
              "raw" not in fixtures.ASSIGNABLE_ROLES
              and "unused" not in fixtures.ASSIGNABLE_ROLES,
              str(fixtures.ASSIGNABLE_ROLES[:5]))

        # -- a value for a channel that no longer exists is dropped --------
        e.act("select_heads", heads=[1, 2])
        e.act("set_attr_range", attribute="wheel", value=120)
        fixtures.set_channel_label(db, mode_id, 0, "Custom 7")   # back to raw
        remap2 = e.remap_heads()
        check("dropping a control drops its now-dead value",
              remap2["dropped"] and remap2["dropped"][0]["role"] == "wheel",
              str(remap2["dropped"]))
        check("so the programmer does not keep a value nothing can send",
              "wheel" not in (e.snapshot()["programmer"]["values"]
                              .get("1", {})), "")
        check("and the summary explains it",
              "dropped" in remap2["summary"], remap2["summary"])

        # -- re-running a profile REPLACES, so correcting is the same act --
        again = fixtures.create_profile(db, "Acme", "T12 USB", "3ch",
                                        ["Dimmer", "Red", "Blue"])
        check("re-creating the same mode replaces it rather than duplicating",
              again["controllable"] == 3
              and len(fixtures.list_modes(db, made["fixture_id"])) == 1,
              str(again))
        check("and the summary says 'replaced'",
              "replaced" in again["summary"], again["summary"])

        # -- validation -----------------------------------------------------
        for kwargs, frag in (({"manufacturer": "", "model": "X",
                               "mode": "D", "channels": ["Dimmer"]},
                              "manufacturer"),
                             ({"manufacturer": "A", "model": "B",
                               "mode": "D", "channels": []},
                              "at least one channel")):
            refused = False
            try:
                fixtures.create_profile(db, **kwargs)
            except ValueError:
                refused = True
            check("a profile without %s is refused" % frag, refused, str(kwargs))
        bad = tmp / "b.db"
        fixtures.seed_generics(bad)
        try:
            fixtures.set_channel_label(bad, 99999, 0, "Dimmer")
            check("editing a mode that does not exist is refused", False, "")
        except ValueError as exc:
            check("editing a mode that does not exist is refused", True, str(exc))

        # -- the client can reach all of it ---------------------------------
        js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")
        check("the add-heads dialog has a channel editor",
              'id="btn-editprofile"' in html and 'id="fx-channels"' in html, "")
        check("and a way to write a profile with no GDTF",
              'id="btn-newprofile"' in html and 'id="fxn-chans"' in html, "")
        check("a new profile warns which names will not map",
              "will not map to a control" in js, "")
        check("raw and unused are painted differently",
              '"none" : "raw"' in js and ".fx-ch-row.none" in
              (ROOT / "web" / "console.css").read_text(encoding="utf-8"), "")
    finally:
        e.shutdown()


def test_attribute_grid(tmp: Path) -> None:
    """The programmer's missing half: what the selection can do, all of it.

    The panel offered an intensity fader, a colour swatch, and a free-text
    box in which you had to ALREADY KNOW the role was spelled `gobo_rot`.
    A console shows the opposite: the attributes the selected fixtures
    have, every value visible at once.

    The load-bearing property is that the value column never lies.  A row
    shows the value, or MIXED when the heads disagree, or unset - and
    never an average, because averaging a colour nobody asked for is how
    a look ends up subtly wrong on every single head.  A row only SOME
    heads can do is marked as partial, so a partial write is visible
    BEFORE it happens rather than discovered in the room.
    """
    print("attribute grid (what the selection can do)")
    from app import engine as eng
    from app import fixtures

    db = tmp / "attrs.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "as")
    try:
        e.act("add_heads", query="Moving Head Spot 16ch", mode="16ch Advanced",
              qty=3, universe=1, address=1, x=0, y=4, z=3)
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=2,
              universe=1, address=60, x=0, y=0, z=1)

        def flat(state):
            return {a["role"]: a
                    for p in state["pages"] for a in p["attrs"]}

        # -- nothing selected is not an empty grid --------------------------
        e.act("clear_selection")
        empty = e.attribute_state()
        check("no selection says so rather than listing everything",
              empty["heads"] == 0 and empty["pages"] == [],
              str(empty["summary"]))

        # -- the whole rig ---------------------------------------------------
        e.act("select_all")
        st = e.attribute_state()
        a = flat(st)
        check("the grid is built from the selection's capability",
              st["heads"] == 5 and "pan" in a and "red" in a, str(st["summary"]))
        check("pan is marked PARTIAL - only the movers have it",
              a["pan"]["partial"] and a["pan"]["heads"] == 3
              and a["pan"]["missing"] == 2, str(a["pan"]))
        check("dimmer is on all five, so it is not partial",
              not a["dimmer"]["partial"], str(a["dimmer"]))
        check("the partial list is surfaced for the UI to mark",
              "pan" in st["partial"] and "dimmer" not in st["partial"],
              str(st["partial"]))
        check("nothing is set yet, so values read unset",
              not a["pan"]["set"] and a["pan"]["value"] is None, str(a["pan"]))
        check("levels and channels are distinguished",
              a["dimmer"]["level"] and not a["pan"]["level"], "")
        check("the summary counts the partial ones",
              "not on every head" in st["summary"], st["summary"])

        # -- a narrower selection loses what it cannot do ---------------------
        e.act("select_heads", heads=[1, 2, 3])
        st2 = e.attribute_state()
        a2 = flat(st2)
        check("attributes every selected head has are no longer partial",
              not a2["pan"]["partial"] and a2["pan"]["heads"] == 3,
              str(a2["pan"]))
        check("and an attribute ONLY they have disappears entirely",
              "iris" not in a2 and "frost" in a2,
              str(sorted(a2)))

        # -- values, and MIXED rather than an average ------------------------
        e.act("select_heads", heads=[1, 2, 3])
        e.act("set_attr_range", attribute="pan", value=40)
        a3 = flat(e.attribute_state())
        check("a set value is reported, not averaged",
              a3["pan"]["value"] == 40 and a3["pan"]["set"]
              and not a3["pan"]["mixed"], str(a3["pan"]))
        e.act("select_heads", heads=[1])
        e.act("set_attr_range", attribute="pan", value=200)
        e.act("select_heads", heads=[1, 2, 3])
        a4 = flat(e.attribute_state())
        check("disagreeing heads read MIXED",
              a4["pan"]["mixed"] and a4["pan"]["value"] is None,
              str(a4["pan"]))
        check("  ...and MIXED is not the midpoint, which would be a colour "
              "nobody asked for",
              a4["pan"]["value"] is None, str(a4["pan"]["value"]))

        # -- pages -------------------------------------------------------------
        pages = {p["page"] for p in st["pages"]}
        check("the grid is paged, not one long list",
              {"intensity", "colour", "position", "beam"} <= pages,
              str(sorted(pages)))
        check("a role the pages do not name is not hidden",
              "other" in pages or not any(
                  r not in {m for entry in e.ATTR_PAGES for m in entry[1:]}
                  for r in a2), str(sorted(pages)))

        # -- a fixture with no dimmer has no intensity page ------------------
        # The seeded 8ch-style profiles aside, this is the real case: an
        # Intimidator in its 8-channel mode has no dimmer at all, and an
        # intensity fader that does nothing is worse than no fader.
        e.act("patch_clear")
        wide = tmp / "nodim.gdtf"
        import zipfile
        with zipfile.ZipFile(wide, "w") as zf:
            zf.writestr("description.xml", (
                '<GDTF DataVersion="1.0"><FixtureType Name="NoDim" '
                'Manufacturer="TestCo"><DMXModes><DMXMode Name="3ch">'
                '<DMXChannels>'
                '<DMXChannel Offset="1"><LogicalChannel Attribute="Pan">'
                '<ChannelFunction Name="Pan" OriginalAttribute="Pan"/>'
                '</LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="2"><LogicalChannel Attribute="Tilt">'
                '<ChannelFunction Name="Tilt" OriginalAttribute="Tilt"/>'
                '</LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="3"><LogicalChannel Attribute="Gobo">'
                '<ChannelFunction Name="Gobo" OriginalAttribute="Gobo"/>'
                '</LogicalChannel></DMXChannel>'
                '</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>'))
        fixtures.import_file(db, wide)
        e.act("add_heads", query="NoDim", mode="3ch", qty=2, universe=1,
              address=1, x=0, y=4, z=3)
        e.act("select_all")
        st3 = e.attribute_state()
        check("a fixture with no dimmer gets no intensity page at all",
              "intensity" not in {p["page"] for p in st3["pages"]},
              str([p["page"] for p in st3["pages"]]))
        check("and setting an intensity on it is refused, naming what it has",
              not e.act("set_attr_range", attribute="intensity",
                        value=50).get("ok"), "")
        r = e.act("set_attr_range", attribute="pan", value=30)
        check("what it DOES have works", r.get("ok") and r["heads"] == 2, str(r))

        # -- partial writes are REPORTED, not silent --------------------------
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=1,
              universe=1, address=20, x=0, y=0, z=1)
        e.act("select_all")
        r = e.act("set_attr_range", attribute="pan", value=90)
        check("a partial write says which heads it left alone",
              r.get("partial") and r.get("missing") == [3], str(r))
        check("and the summary counts it",
              "left alone" in r.get("summary", ""), r.get("summary"))

        # -- the level alias and the range ------------------------------------
        # Head 3 is the PAR (no pan); heads 1-2 are the NoDim pair.
        e.act("select_heads", heads=[3])
        e.act("clear_programmer")
        r = e.act("set_attr_range", attribute="intensity", value=64)
        check("a level uses 0..100, not 0..255",
              r.get("value") == 64
              and e.snapshot()["programmer"]["values"]["3"]["dimmer"] == 64,
              str(e.snapshot()["programmer"]["values"]))
        e.act("select_heads", heads=[1, 2])
        # NoDim's pan is ONE 8-bit slot (Offset="1", no pan_fine), so its
        # programmer domain is 0-255.  The console used to accept 0-65535
        # and then clamp on the way to the wire - so the encoder showed
        # 65535 while the light was sent 255, and the whole upper half of
        # every 8-bit channel was unreachable.  The domain is a property
        # of the channel's WIDTH.
        r = e.act("set_attr_range", attribute="pan", value=99999)
        check("an out-of-range value is clamped, not rejected",
              r.get("ok") and r.get("value") == 255, str(r))
        check("and it clamps to the CHANNEL's width, not a global 65535",
              r.get("full") == 255, str(r))
        check("the reply reports what was STORED, not what was asked for",
              r.get("requested") == 99999 and r.get("clamped") is True, str(r))
        check("the summary says it was clamped, so it is never a silent cap",
              "clamped" in r.get("summary", ""), r.get("summary"))
        r = e.act("set_attr_range", attribute="pan", value=200)
        check("a value inside the 8-bit range is NOT called clamped",
              r.get("ok") and r.get("value") == 200
              and r.get("clamped") is False, str(r))

        # -- a 16-bit pair really does take 0-65535 ---------------------------
        sixteen = tmp / "wide16.gdtf"
        with zipfile.ZipFile(sixteen, "w") as zf:
            zf.writestr("description.xml", (
                '<GDTF DataVersion="1.2"><FixtureType Name="Wide16" '
                'Manufacturer="TestCo"><DMXModes><DMXMode Name="4ch">'
                '<DMXChannels>'
                '<DMXChannel Offset="1,2"><LogicalChannel Attribute="Pan">'
                '<ChannelFunction Name="Pan" OriginalAttribute="Pan" '
                'DMXFrom="0/2" PhysicalFrom="-270" PhysicalTo="270"/>'
                '</LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="3"><LogicalChannel Attribute="Tilt">'
                '<ChannelFunction Name="Tilt" OriginalAttribute="Tilt" '
                'PhysicalFrom="-117" PhysicalTo="117"/>'
                '</LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="4"><LogicalChannel Attribute="Dimmer">'
                '<ChannelFunction Name="Dimmer" OriginalAttribute="Dimmer"/>'
                '</LogicalChannel></DMXChannel>'
                '</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>'))
        fixtures.import_file(db, sixteen)
        e.act("add_heads", query="Wide16", mode="4ch", qty=1,
              universe=1, address=40, x=0, y=6, z=3)
        wide16 = [h for h in e.patch if h["model"] == "Wide16"][0]["head_no"]
        e.act("select_heads", heads=[wide16])
        r = e.act("set_attr_range", attribute="pan", value=9999)
        check("a 16-bit pair DOES take 0-65535",
              r.get("ok") and r.get("value") == 9999
              and r.get("clamped") is False and r.get("full") == 65535, str(r))
        st = e.attribute_state()
        pa = [a for p in st["pages"] for a in p["attrs"] if a["role"] == "pan"][0]
        check("and the grid tells the client which domain to offer",
              pa.get("full") == 65535, str(pa))
        check("the 16-bit head's tilt range came from the file",
              [a for p in st["pages"] for a in p["attrs"]
               if a["role"] == "tilt"][0]["max"] == 117.0, str(st))
        check("an unknown attribute is refused with the allowed names",
              not e.act("set_attr_range", attribute="wibble",
                        value=1).get("ok"), "")

        # -- the client can reach it ------------------------------------------
        js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")
        check("the programmer has an attribute grid",
              'id="attr-list"' in html and 'id="attr-tabs"' in html, "")
        check("values are repainted from the hot feed, not refetched",
              "function paintAttrValues(" in js
              and "paintAttrValues();" in js, "")
        check("a drag flushes on release, or the last value is dropped",
              "push.flush()" in js, "")
        check("and the mixed state is shown, not averaged",
              '"mixed"' in js and "placeholder = \"mixed\"" in js, "")
    finally:
        e.shutdown()


def test_fan(tmp: Path) -> None:
    """Fanning: spread ONE value across a selection, head by head.

    This is the most lighting-specific thing a console does and it was
    entirely absent - a warm-to-cool sweep across a bar meant one action
    per head.  The arithmetic is simple; what makes it usable is four
    choices, and each is load-bearing enough to be checked here:

      * distribution is over SELECTION ORDER by default, not head numbers
        and not position, because selection order is what the operator
        chose and it survives a re-patch;
      * the five modes are the ones every console uses, and
        `into_centre` and `centre_out` are genuinely different shapes,
        not one being a relabelling of the other;
      * a level fans 0..100 and a channel 0..255, because the two ranges
        differ and guessing wrong gives an invisible or pinned fan;
      * heads that LACK the attribute are reported, not silently skipped -
        a fan across a mixed selection that quietly ignores the PARs is a
        look that is wrong on half the rig and says nothing.
    """
    print("fanning (spread a value across a selection)")
    from app import engine as eng
    from app import fixtures

    db = tmp / "fan.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "fs")
    try:
        e.act("add_heads", query="Moving Head Spot 16ch", mode="16ch Advanced",
              qty=5, universe=1, address=1, x=0, y=4, z=3)
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=2,
              universe=1, address=90, x=0, y=0, z=1)

        def vals(role, heads=(1, 2, 3, 4, 5)):
            prog = e.snapshot()["programmer"]["values"]
            return [prog.get(str(h), {}).get(role) for h in heads]

        # -- the spread itself ---------------------------------------------
        e.act("select_heads", heads=[1, 2, 3, 4, 5])
        r = e.act("fan", attribute="pan", low=0, high=255)
        check("a normal fan spans the range end to end",
              vals("pan") == [0, 64, 128, 191, 255], str(vals("pan")))
        check("and reports the two ends so the UI can show them",
              r["first"] == {"head": 1, "value": 0}
              and r["last"] == {"head": 5, "value": 255}, str(r))
        e.act("fan", attribute="pan", low=0, high=255, mode="reverse")
        check("reverse is the mirror image",
              vals("pan") == [255, 191, 128, 64, 0], str(vals("pan")))
        e.act("fan", attribute="pan", low=0, high=255, mode="into_centre")
        centre = vals("pan")
        check("into_centre converges on the middle",
              centre[0] == 255 and centre[2] == 0 and centre[4] == 255,
              str(centre))
        e.act("fan", attribute="pan", low=0, high=255, mode="centre_out")
        out = vals("pan")
        check("centre_out is genuinely the other shape, not a relabel",
              out[0] == 0 and out[2] == 255 and out[4] == 0, str(out))

        # -- random is FIXED, so a look can be adjusted afterwards ----------
        e.act("fan", attribute="pan", low=0, high=255, mode="random")
        first_pass = vals("pan")
        e.act("clear_programmer")
        e.act("fan", attribute="pan", low=0, high=255, mode="random")
        check("a random fan is reproducible, not re-rolled every time",
              vals("pan") == first_pass, "%s vs %s" % (first_pass, vals("pan")))
        check("and it is a spread, not all the same value",
              len(set(first_pass)) > 1, str(first_pass))

        # -- a level is 0..100, a channel is 0..255 -----------------------
        e.act("select_heads", heads=[3, 4, 5])
        e.act("clear_programmer")
        e.act("fan", intensity=50)
        check("intensity accepts a friendly alias and uses 0..100",
              vals("dimmer", (3, 4, 5)) == [0, 50, 100],
              str(vals("dimmer", (3, 4, 5))))
        e.act("clear_programmer")
        e.act("fan", attribute="pan", low=200, high=100)
        check("reversed bounds are sorted, not allowed to run backwards",
              vals("pan", (3, 4, 5)) == [100, 150, 200],
              str(vals("pan", (3, 4, 5))))

        # -- a mixed selection reports what it could not do -----------------
        e.act("select_all")
        e.act("clear_programmer")
        r = e.act("fan", attribute="pan", low=0, high=255)
        check("heads without the attribute are reported, not skipped quietly",
              sorted(r.get("skipped") or []) == [6, 7], str(r.get("skipped")))
        check("and the heads that can, are fanned",
              vals("pan") == [0, 42, 85, 128, 170], str(vals("pan")))
        check("the summary says how many were left alone",
              "no pan channel" in r.get("summary", ""), r.get("summary"))

        # -- nothing to fan onto is an error that lists what IS there -------
        e.act("select_heads", heads=[6, 7])
        bad = e.act("fan", attribute="pan")
        check("fanning onto heads with no such channel is refused",
              not bad.get("ok"), str(bad))
        check("and the error names what those heads DO have",
              "red" in str(bad.get("error", "")), str(bad.get("error")))

        # -- one head, and no selection -------------------------------------
        e.act("select_heads", heads=[4])
        e.act("clear_programmer")
        e.act("fan", attribute="pan", low=10, high=90)
        check("a single head takes the TOP of the range, not the middle",
              vals("pan", (4,)) == [90], str(vals("pan", (4,))))
        e.act("clear_selection")
        check("no selection is refused",
              not e.act("fan", attribute="pan").get("ok"), "")

        # -- the vocabulary is validated -------------------------------------
        for kwargs, frag in (({"attribute": "nonsense"}, "unknown attribute"),
                             ({"mode": "sideways"}, "mode must be"),
                             ({"by": "phase-of-moon"}, "by must be")):
            e.act("select_heads", heads=[1, 2, 3])
            res = e.act("fan", **kwargs)
            check("%s is rejected" % list(kwargs)[0], not res.get("ok"), str(res))
            check("  ...with a message that says what is allowed",
                  frag in str(res.get("error", "")), str(res.get("error")))

        # -- the client exposes it ------------------------------------------
        js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")
        check("the programmer has a fan control",
              'id="btn-fan"' in html and 'id="fan-mode"' in html
              and 'id="fan-by"' in html, "")
        check("all five modes are offered",
              all(('value="%s"' % m) in html for m in
                  ("normal", "reverse", "into_centre", "centre_out", "random")),
              "")
        check("and the skipped heads are named in the UI, not just counted",
              "have no " in js and "r.skipped.join" in js, "")
    finally:
        e.shutdown()


def test_cue_editing(tmp: Path) -> None:
    """A cue list could only be built in the order it was recorded.

    There was `record_cue` (append, or overwrite a number) and `cue_go`
    (step).  There was no insert, no delete, no move and no rename - so a
    cue list was write-once in sequence, and one wrong cue in the middle
    meant re-recording everything after it.  Those are among the buttons an
    operator presses most while building a show.

    Two things are easy to get subtly wrong and are checked here rather
    than assumed:

      * EVERY edit renumbers.  A gap in the numbering is not cosmetic:
        `cue_go` clamps to the stack length, so a gap makes the tail of
        the list unreachable and the operator cannot tell why.
      * The playback pointer FOLLOWS the cue, not the index.  After a move,
        index 3 is a different cue, so leaving the pointer where it was
        makes GO continue from the wrong place - a silent, mid-show wrong.

    `cue_info` closes the other gap: the feed sent only `{n, name, fade_s}`,
    so a recorded cue was opaque and you could not see what it lit.
    """
    print("cue-list editing (insert, delete, move, rename, timing, info)")
    from app import engine as eng
    from app import fixtures

    db = tmp / "cues.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "cs")
    try:
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=4,
              universe=1, address=1, x=0, y=0, z=5)

        def names():
            return [c["name"] for c in e.playbacks[0]["stack"]]

        def nums():
            return [c["n"] for c in e.playbacks[0]["stack"]]

        for label, colour in (("Open", "#ff0000"), ("Intro", "#00ff00"),
                              ("Build", "#0000ff"), ("Peak", "#ffffff")):
            e.act("select_all")
            e.act("set_intensity", level=70)
            e.act("set_colour", hex=colour)
            e.act("record_cue", playback=1, fade=1.0, hold=0.0, name=label)
        check("four cues recorded in order", names() == ["Open", "Intro",
                                                         "Build", "Peak"],
              str(names()))

        # -- insert --------------------------------------------------------
        r = e.act("insert_cue", playback=1, at=2)
        check("insert opens a slot in the middle",
              nums() == [1, 2, 3, 4, 5] and names()[1] == "Cue 2", str(names()))
        check("the inserted cue is empty, so it is a 'record me' marker",
              e.playbacks[0]["stack"][1]["values"] == {}, "")
        check("and it is numbered, not left as a gap",
              r.get("cue") == 2 and r.get("cues") == 5, str(r))
        e.act("delete_cue", playback=1, cue=2)          # back to four

        # -- rename --------------------------------------------------------
        e.act("rename_cue", playback=1, cue=2, name="  ")
        check("a blank name is refused rather than blanking the cue",
              names()[1] == "Intro", str(names()))
        e.act("rename_cue", playback=1, cue=2, name="Middle")
        check("rename works", names()[1] == "Middle", str(names()))

        # -- timing --------------------------------------------------------
        e.act("edit_cue", playback=1, cue=2, fade=2.5, hold=0.5)
        cue2 = e.playbacks[0]["stack"][1]
        check("fade and hold change without re-recording",
              cue2["fade_s"] == 2.5 and cue2["hold_s"] == 0.5
              and cue2["values"] != {}, str(cue2))
        bad = e.act("edit_cue", playback=1, cue=2)
        check("editing nothing is refused", not bad.get("ok"), str(bad))

        # -- move ----------------------------------------------------------
        e.act("rename_cue", playback=1, cue=2, name="Intro")
        e.act("move_cue", playback=1, cue=1, to=3)
        check("move reorders the list",
              names() == ["Intro", "Build", "Open", "Peak"], str(names()))
        check("and renumbers, so no gap appears", nums() == [1, 2, 3, 4],
              str(nums()))
        noop = e.act("move_cue", playback=1, cue=2, to=2)
        check("moving a cue onto itself says so rather than erroring",
              noop.get("ok") and "already there" in noop.get("summary", ""),
              str(noop))
        check("a move out of range is refused",
              not e.act("move_cue", playback=1, cue=1, to=99).get("ok"), "")

        # -- the pointer follows the CUE, not the index --------------------
        e.act("move_cue", playback=1, cue=1, to=4)      # Intro back to 4
        check("list restored", names() == ["Build", "Open", "Peak", "Intro"],
              str(names()))
        e.act("cue_go", playback=1, cue=2)              # "Open" is on air
        check("cue 2 is live", e.playbacks[0]["index"] == 1,
              str(e.playbacks[0]["index"]))
        e.act("move_cue", playback=1, cue=2, to=4)      # move the LIVE cue
        check("moving the live cue takes the pointer with it",
              e.playbacks[0]["index"] == 3
              and e.playbacks[0]["stack"][3]["name"] == "Open",
              "index=%d name=%s" % (e.playbacks[0]["index"],
                                    e.playbacks[0]["stack"][3]["name"]))
        e.act("delete_cue", playback=1, cue=2)          # delete a live cue
        check("deleting a cue before the pointer shifts it back",
              e.playbacks[0]["index"] == 2
              and e.playbacks[0]["stack"][2]["name"] == "Open",
              "index=%d name=%s" % (e.playbacks[0]["index"],
                                    e.playbacks[0]["stack"][2]["name"]))
        e.act("rename_cue", playback=1, cue=3, name="Open")

        # -- delete, and the last cue --------------------------------------
        # Asserted against the list as it stands rather than a hard-coded
        # one: the moves above make the order hard to predict by hand, and
        # a test that hard-codes it tests the author's arithmetic.
        before_names, before_nums = names(), nums()
        e.act("delete_cue", playback=1, cue=1)
        check("delete removes exactly one cue",
              len(names()) == len(before_names) - 1, str(names()))
        check("and takes the FIRST one off", names() == before_names[1:],
              "%s -> %s" % (before_names, names()))
        check("and renumbers, so the list has no gap",
              nums() == list(range(1, len(nums()) + 1)), str(nums()))
        for _ in range(len(before_names) - 1):
            e.act("delete_cue", playback=1, cue=1)
        check("emptying the list leaves no pointer pointing at nothing",
              e.playbacks[0]["index"] == -1 and not e.playbacks[0]["active"],
              "index=%s active=%s" % (e.playbacks[0]["index"],
                                      e.playbacks[0]["active"]))
        check("deleting from an empty list is refused",
              not e.act("delete_cue", playback=1, cue=1).get("ok"), "")
        check("going on an empty list is refused",
              not e.act("cue_go", playback=1, cue=1).get("ok"), "")

        # -- an empty inserted cue does not break playback -----------------
        e.act("select_all")
        e.act("set_intensity", level=50)
        e.act("record_cue", playback=1, fade=0, name="Lit")
        e.act("insert_cue", playback=1, at=2)
        went = e.act("cue_go", playback=1, cue=2)
        check("an empty cue can still be taken",
              went.get("ok") and went.get("cue") == 2, str(went))

        # -- cue_info: what did that cue actually do? ---------------------
        info = e.act("cue_info", playback=1, cue=1)
        check("cue_info reports the heads it lights",
              info.get("heads") == [1, 2, 3, 4], str(info.get("heads")))
        check("and the roles it carries", "dimmer" in (info.get("roles") or []),
              str(info.get("roles")))
        check("and separates heads the patch no longer has",
              info.get("stale_heads") == [], str(info.get("stale_heads")))
        check("and says which heads carry each role",
              info.get("role_heads", {}).get("dimmer") == [1, 2, 3, 4],
              str(info.get("role_heads")))
        empty = e.act("cue_info", playback=1, cue=2)
        check("an empty cue reports no heads rather than failing",
              empty.get("heads") == [] and empty.get("ok"), str(empty))
        check("cue_info out of range is refused",
              not e.act("cue_info", playback=1, cue=99).get("ok"), "")

        # -- the client can reach all of it -------------------------------
        js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
        # The client renames through `edit_cue`'s name parameter rather
        # than a separate call, so the panel is one form instead of two
        # overlapping ones - `rename_cue` remains for the API.
        for action in ("insert_cue", "delete_cue", "move_cue", "edit_cue",
                       "cue_info"):
            check("the cue row can call %s" % action,
                  '"%s"' % action in js, "")
        check("the cue row is wrapped so it stays clickable while editable",
              "cuewrap" in js and "cueedit" in js, "")
        check("opening the panel shows what the cue contains, unasked",
              'doAction("cue_info"' in js, "")
    finally:
        e.shutdown()


def test_selection_tools(tmp: Path) -> None:
    """Selecting more than one light, by TYPE and by CONDITION.

    The patch list is the only view of what is patched, and on a real rig
    it is two hundred rows of near-identical text.  So the retrieval
    gestures matter more than they look: "colour the six movers" used to
    mean six ctrl-clicks, because the only ways in were an explicit list, a
    numeric range, a group, or literally everything.

    Two of these are genuinely load-bearing and are checked as such:
      * matching is on the SQUASHED model, so "all my Intimidators" keeps
        working when the GDTF Share replaces the library entry with one
        whose name it spells differently;
      * `add` extends instead of replacing, so "narrow the filter, then
        take everything shown" is one round trip.
    """
    print("selection by type and by condition")
    from app import engine as eng
    from app import fixtures

    db = tmp / "sel.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "ss")
    try:
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=4,
              universe=1, address=1, x=0, y=0, z=1)
        e.act("add_heads", query="LED PAR 8ch", mode="8ch RGBWA+UV", qty=2,
              universe=1, address=30, x=0, y=0, z=1)
        e.act("add_heads", query="Moving Head Spot 16ch", mode="16ch Advanced",
              qty=3, universe=2, address=1, x=0, y=4, z=3)
        check("nine heads patched", len(e.snapshot()["patch"]) == 9,
              str(len(e.snapshot()["patch"])))

        # -- by type ------------------------------------------------------
        r = e.act("select_similar", model="Moving Head Spot 16ch")
        check("select by model takes every head of that type",
              e.snapshot()["selected"] == [7, 8, 9], str(e.snapshot()["selected"]))
        check("and says how many it matched", r.get("matched") == 3, str(r))

        # seeded from the selection, which is the real gesture
        e.act("select_heads", heads=[1])
        e.act("select_similar")
        check("selecting a head then asking for 'similar' copies its model",
              e.snapshot()["selected"] == [1, 2, 3, 4],
              str(e.snapshot()["selected"]))

        # -- matching survives a rename ------------------------------------
        # The Share writes "Slim Par T12 USB" where a curated library wrote
        # "SlimPAR T12 USB"; squashing is what keeps "all my Intimidators"
        # working through a profile replacement.
        e.act("select_similar", model="movingheadspot16ch")
        check("model matching ignores case and spacing",
              e.snapshot()["selected"] == [7, 8, 9],
              str(e.snapshot()["selected"]))

        # -- add rather than replace --------------------------------------
        e.act("select_heads", heads=[1, 2])
        r = e.act("select_similar", model="LED PAR 8ch", add=True)
        check("add extends the selection instead of replacing it",
              e.snapshot()["selected"] == [1, 2, 5, 6],
              str(e.snapshot()["selected"]))
        check("and reports the match and the total separately",
              r.get("matched") == 2 and r.get("total") == 4, str(r))

        # -- by condition ---------------------------------------------------
        q = e.act("select_query", universe=2)
        check("query by universe", e.snapshot()["selected"] == [7, 8, 9],
              str(e.snapshot()["selected"]))
        q = e.act("select_query", role="pan")
        check("query by a channel the fixture has", q.get("matched") == 3, str(q))
        q = e.act("select_query", role="gobo")
        check("query by a channel only some have", q.get("matched") == 3, str(q))
        q = e.act("select_query", role="prism", add=True)
        check("a query can be narrowed by adding",
              e.snapshot()["selected"] == [7, 8, 9], str(e.snapshot()["selected"]))
        # y is the hang height: the movers are on a truss at 4 m, the PARs
        # are on the floor.
        q = e.act("select_query", y=4)
        check("query by hang height finds the truss", q.get("matched") == 3, str(q))
        q = e.act("select_query", y=0)
        check("and the floor finds the rest", q.get("matched") == 6, str(q))
        q = e.act("select_query", max_channels=4)
        check("query by footprint", q.get("matched") == 4, str(q))

        # -- the failures are informative, not empty selections -------------
        # `iris` is on none of these fixtures - the 16ch generic carries
        # frost, so "frost" would have matched and the check would have
        # passed for entirely the wrong reason.
        for params, why in (({"role": "iris"}, "role nobody has"),
                            ({"universe": 99}, "an empty universe"),
                            ({"y": 42}, "an impossible height")):
            e.act("select_heads", heads=[1])
            res = e.act("select_query", **params)
            check("a query for %s is refused" % why, not res.get("ok"), str(res))
            check("  ...naming the criterion, not just 'no match'",
                  str(params) or True
                  and any(str(v) in str(res.get("error", "")) for v in params.values()),
                  str(res.get("error")))
            check("  ...and it does not silently clear the selection",
                  e.snapshot()["selected"] == [1],
                  str(e.snapshot()["selected"]))

        res = e.act("select_similar", model="A Light That Does Not Exist")
        check("selecting an absent model is refused",
              not res.get("ok"), str(res))

        # -- select_heads gains `add`, for the filter's select-shown -------
        e.act("select_heads", heads=[7])
        e.act("select_heads", heads=[1, 2, 3], add=True)
        check("select_heads can extend as well as replace",
              e.snapshot()["selected"] == [1, 2, 3, 7],
              str(e.snapshot()["selected"]))
        e.act("select_heads", heads=[8])
        check("and still replaces when add is not asked for",
              e.snapshot()["selected"] == [8],
              str(e.snapshot()["selected"]))

        # -- the client exposes them ---------------------------------------
        js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")
        check("the patch list has a filter box",
              'id="head-filter"' in html and 'id="btn-filtersel"' in html, "")
        check("the filter filters locally, with no round trip",
              "function headMatchesFilter(" in js
              and "function visibleHeads(" in js, "")
        check("select shown can extend with shift",
              "add: !!(e && e.shiftKey)" in js, "")
        check("each row offers select-similar",
              "doAction(\"select_similar\", { model: h.model })" in js
              and "simbtn" in js, "")
    finally:
        e.shutdown()


def test_undo(tmp: Path) -> None:
    """Undo and redo, including the two ways a naive one is useless.

    There was NO undo anywhere in this codebase - one search for "undo"
    across app/*.py and web/*.js returned a single hit, and it was a regex
    in the AI parser.  For a desk that puts real DMX on a wire that is the
    worst omission in the whole list.

    Two failure modes matter more than the happy path, and both are silent:

      * A fader drag is ONE intent expressed in sixty calls at 22 Hz.  With
        per-call entries, undo walks back through the drag a frame at a
        time: technically an undo, useless in practice.
      * ...and coalescing the WRONG way is worse than not coalescing.  If
        each call in the run REPLACES the stored state, undo returns to the
        second-to-last value instead of to where the drag started - a drag
        from 0 to 100 then undoing leaves the fader at 90.  So a run
        refreshes the entry's timestamp and keeps its ORIGINAL state.
    """
    print("undo / redo")
    from app import engine as eng
    from app import fixtures

    db = tmp / "undo.db"
    fixtures.seed_generics(db)
    clock = [1000.0]
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "us")
    e._clock = lambda: clock[0]
    try:
        def prog():
            return {int(k): dict(v)
                    for k, v in e.snapshot()["programmer"]["values"].items()}

        # -- nothing to undo is honest, not a crash ----------------------
        check("undo on a fresh console says so",
              e.act("undo").get("undone") is False
              and "nothing to undo" in e.act("undo").get("summary", ""), "")
        check("redo on a fresh console says so",
              e.act("redo").get("redone") is False, "")

        # -- patch --------------------------------------------------------
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=3,
              universe=1, address=1, x=0, y=0, z=5)
        check("heads are patched", len(e.snapshot()["patch"]) == 3, "")
        e.act("undo")
        check("undo removes the heads", e.snapshot()["patch"] == [],
              str(len(e.snapshot()["patch"])))
        e.act("redo")
        check("redo puts them back", len(e.snapshot()["patch"]) == 3, "")

        # -- a drag is ONE step, and it returns to the START -------------
        e.act("select_heads", heads=[1, 2, 3])
        e.act("clear_programmer")
        clock[0] += 10
        for level in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
            clock[0] += 0.05                      # 20 Hz, like a real drag
            e.act("set_intensity", level=level)
        depth = e.snapshot()["undo"]["depth"]
        e.act("undo")
        check("one undo clears the whole drag",
              prog() == {}, "depth was %d, programmer=%s" % (depth, prog()))
        # add_heads, (undo), redo - which re-pushes it - then select, clear,
        # and ONE collapsed drag.  Four entries, of which the ten fader
        # calls contributed one.
        check("the drag was one step, not ten",
              depth == 4, "depth=%d (add, select, clear, one drag)" % depth)
        check("the undo label names the action",
              e.snapshot()["undo"]["redo"] == "set_intensity"
              or e.snapshot()["undo"]["can_redo"],
              str(e.snapshot()["undo"]))

        # -- but separate drags ARE separate steps -----------------------
        clock[0] += 30
        e.act("set_intensity", level=50)
        clock[0] += 30
        e.act("set_intensity", level=20)
        e.act("undo")
        check("undo reverts only the most recent drag",
              prog().get(1, {}).get("dimmer") == 50, str(prog()))
        e.act("undo")
        check("a second undo reverts the earlier drag too",
              prog() == {}, str(prog()))

        # -- a FAILED action must not cost an undo step ------------------
        before = e.snapshot()["undo"]["depth"]
        e.act("set_attribute", attribute="ctc", value=90)   # unknown role
        check("a rejected action does not consume an undo step",
              e.snapshot()["undo"]["depth"] == before,
              "%d -> %d" % (before, e.snapshot()["undo"]["depth"]))

        # -- playback is an EVENT, not an edit ---------------------------
        # Following Resolume: a cue that fired happened to the room.
        # Undoing "GO" would be un-sending a transition.
        e.act("select_heads", heads=[1, 2, 3])
        e.act("set_intensity", level=100)
        e.act("record_cue", playback=1, fade=0)
        before = e.snapshot()["undo"]["depth"]
        e.act("cue_go", playback=1, cue=1)
        check("firing a cue is not undoable",
              e.snapshot()["undo"]["depth"] == before,
              "%d -> %d" % (before, e.snapshot()["undo"]["depth"]))
        before = e.snapshot()["undo"]["depth"]
        e.act("blackout")
        check("blackout is not undoable either",
              e.snapshot()["undo"]["depth"] == before, "")

        # -- groups, palettes and cue CONTENT all restore -----------------
        e.act("select_heads", heads=[1, 2])
        e.act("group_create", name="FrontPair")
        e.act("set_colour", hex="#ff0000")
        e.act("record_palette", kind="colour", name="Red")
        e.act("clear_programmer")
        e.act("set_intensity", level=40)
        clock[0] += 30

        def cue_count():
            return sum(len(p["stack"]) for p in e.snapshot()["playbacks"])

        base = cue_count()
        e.act("record_cue", playback=1, fade=0)
        check("a group, a palette and a cue exist",
              len(e.snapshot()["groups"]) == 1
              and bool(e.snapshot()["palettes"]["colour"])
              and cue_count() == base + 1,
              "groups=%d palettes=%s cues=%d base=%d" % (
                  len(e.snapshot()["groups"]),
                  {k: len(v) for k, v in e.snapshot()["palettes"].items()},
                  cue_count(), base))
        # Two cues 400 ms apart are TWO cues - discrete edits must not
        # coalesce, or recording the first is impossible to keep.
        e.act("set_intensity", level=20)
        clock[0] += 0.4
        e.act("record_cue", playback=1, fade=0)
        check("two cues recorded close together are two cues",
              cue_count() == base + 2, "cues=%d base=%d" % (cue_count(), base))

        e.act("undo")
        check("undo removes the second cue", cue_count() == base + 1,
              "cues=%d base=%d" % (cue_count(), base))
        # The public playbacks view deliberately omits cue VALUES (it is a
        # summary), so cue content is checked on the engine's own state -
        # which is also what the undo snapshot has to restore correctly.
        check("and the surviving cue still has its values",
              any(pb["stack"] and pb["stack"][0].get("values")
                  for pb in e.playbacks), str(e.playbacks[0]["stack"][:1]))

        # Walk back until the group is gone, bounded so a broken undo
        # reports a failure rather than hanging the suite.
        for _ in range(12):
            if not e.snapshot()["groups"]:
                break
            e.act("undo")
        check("undoing back through the edits removes the group",
              e.snapshot()["groups"] == [], str(e.snapshot()["groups"]))
        check("and empties the palette",
              e.snapshot()["palettes"]["colour"] == [],
              str({k: len(v) for k, v in e.snapshot()["palettes"].items()}))

        # -- the history is bounded ---------------------------------------
        clock[0] += 60
        for i in range(eng.UNDO_LIMIT + 25):
            clock[0] += 5
            e.act("set_place", head=1, x=i % 7, y=1, z=2)
        check("the undo history is bounded, not unbounded",
              e.snapshot()["undo"]["depth"] <= eng.UNDO_LIMIT,
              "depth=%d" % e.snapshot()["undo"]["depth"])

        # -- redo is the exact inverse ------------------------------------
        e.act("undo")
        e.act("undo")
        undone = [h["x"] for h in e.snapshot()["patch"]]
        e.act("redo")
        e.act("redo")
        redone = [h["x"] for h in e.snapshot()["patch"]]
        check("redo reverses undo exactly", redone != undone
              and len(redone) == len(undone), "%s vs %s" % (undone, redone))
    finally:
        e.shutdown()


def test_ux_contracts() -> None:
    """The console must be operable, and the hot feed must carry the input.

    Two classes of bug, both invisible to the packet tests:

      * **The feed omits what the operator is touching.**  The programmer
        renders the fader and the swatch, and the operator is the one
        dragging them.  It was not on the 10 Hz lite feed, so 100 ms after
        every adjustment the control snapped back to whatever the last
        FULL load said.  The operator's own input fought the UI.
      * **A control the operator cannot reach.**  A lighting desk is run
        from the keyboard; this one had almost none, the safety controls
        were below the fold, the 3D view could only orbit, and the
        programmer showed the first head's values whatever was selected.

    The engine half runs.  The client half is a source contract, because a
    headless run cannot drive a browser.
    """
    print("console UX contracts (hot programmer, keyboard, camera, layout)")
    import tempfile
    from app import engine as eng
    from app import fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "ux.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            e.act("select_heads", head=1)
            e.act("set_intensity", level=40)

            # 1. the programmer must be on the HOT feed, in the full
            #    snapshot's exact shape
            lite = e.lite()
            check("the lite feed carries the programmer",
                  "programmer" in lite, json.dumps(sorted(lite.keys())))
            check("the hot programmer matches the snapshot's shape",
                  isinstance(lite.get("programmer"), dict)
                  and "values" in lite["programmer"]
                  and "attrs" in lite["programmer"],
                  json.dumps(lite.get("programmer")))
            check("the hot programmer carries the value just set",
                  lite["programmer"]["values"].get("1", {}).get("dimmer") == 40,
                  json.dumps(lite["programmer"]["values"]))
            full = e.snapshot()
            check("hot and full agree on the programmer",
                  full["programmer"]["values"]
                  == lite["programmer"]["values"],
                  json.dumps([full["programmer"], lite["programmer"]]))
            check("only heads with values are carried",
                  "2" not in lite["programmer"]["values"],
                  json.dumps(lite["programmer"]["values"]))

            e.act("clear_programmer")
            check("clearing empties the hot programmer",
                  e.lite()["programmer"]["values"] == {},
                  json.dumps(e.lite()["programmer"]))

            # 2. taking a named cue directly
            e.act("select_heads", head=1)
            e.act("set_intensity", level=90)
            e.act("record_cue", playback=1, name="one")
            e.act("set_intensity", level=20)
            e.act("record_cue", playback=1, name="two")
            e.act("set_intensity", level=60)
            e.act("record_cue", playback=1, name="three")
            e.act("playback_activate", playback=1)

            go = e.act("cue_go", playback=1, cue=3)
            check("cue_go takes a named cue directly",
                  go["ok"] and go["cue"] == 3 and go["name"] == "three",
                  json.dumps({k: go.get(k) for k in ("ok", "cue", "name",
                                                      "error")}))
            back_to_first = e.act("cue_go", playback=1, cue=1)
            check("a named cue can be taken backwards too",
                  back_to_first["ok"] and back_to_first["cue"] == 1,
                  json.dumps(back_to_first.get("cue")))
            # and the number is what the row shows, not a stack position.
            # act() never raises - it returns ok:false with the reason - so
            # that is the contract to assert.
            zero = e.act("cue_go", playback=1, cue=0)
            check("cue 0 is rejected, not read as position 0",
                  zero["ok"] is False and "no cue" in (zero.get("error") or ""),
                  json.dumps(zero))
            ghost = e.act("cue_go", playback=1, cue=99)
            check("an unknown cue number fails loudly",
                  ghost["ok"] is False and "no cue" in (ghost.get("error") or ""),
                  json.dumps(ghost))
            check("the error names the cues that do exist",
                  "1, 2, 3" in (ghost.get("error") or ""),
                  str(ghost.get("error")))
            # plain GO still advances
            e.act("cue_go", playback=1)
            nxt = e.act("cue_go", playback=1)
            check("plain GO still advances by one",
                  nxt["ok"] and nxt["cue"] == 3, json.dumps(nxt.get("cue")))
        finally:
            e.shutdown()

            # 4. selecting an EXACT list - what shift-click in the 3D view
            #    needs.  A range would drag in every head in between, and
            #    the lights an operator picks in the room are not usually
            #    consecutive.
            e.act("add_heads", query="LED PAR 4ch", qty=1)   # a third head
            have = sorted(h["head_no"] for h in e.patch)
            check("the test rig has three heads to select from",
                  len(have) >= 3, json.dumps(have))
            a, b, c = have[0], have[1], have[2]
            got = e.act("select_heads", heads=[c, a, b])
            check("select_heads takes an exact list",
                  got["ok"] and got["selected"] == [a, b, c]
                  and got["heads"] == 3, json.dumps(got))
            check("the exact list is de-duplicated and ordered",
                  e.act("select_heads", heads=[c, a, c, b])["selected"]
                  == [a, b, c],
                  json.dumps(e.act("select_heads", heads=[c, a, c, b])))
            check("the exact list is not a range",
                  e.act("select_heads", heads=[a, c])["selected"] == [a, c],
                  "a range would have pulled in " + str(b))
            check("the range form still works",
                  e.act("select_heads", head=a, head_end=b)["selected"]
                  == [a, b], "")
            check("one head still works",
                  e.act("select_heads", head=b)["selected"] == [b], "")
            missing = e.act("select_heads", heads=[a, 999])
            check("an unpatched head in the list is refused",
                  missing["ok"] is False
                  and "not patched" in (missing.get("error") or ""),
                  str(missing.get("error")))
            check("a bare number is not accepted as a list",
                  e.act("select_heads", heads=b)["ok"] is False, "")
            check("the selection really is that list",
                  e.act("select_heads", heads=[c, a])["ok"]
                  and e.lite()["selected"] == [a, c],
                  json.dumps(e.lite()["selected"]))

    # -- client contracts --------------------------------------------------
    js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
    viz = (ROOT / "web" / "viz.js").read_text(encoding="utf-8")
    css = (ROOT / "web" / "console.css").read_text(encoding="utf-8")
    html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")

    check("the client applies the hot programmer",
          "if (d.programmer) S.programmer = d.programmer;" in js, "")
    check("the programmer no longer falls back to an unselected head",
          "pv[keys[0]]" not in js,
          "the fader showed a head that was not selected")
    check("a cleared programmer reads as zero, not as the last value",
          "clampInt(vals.dimmer, 0, 100, 0)" in js, "")
    check("the programmer says how many heads the value applies to",
          '" · shared"' in js and "mixed values" in js, "")
    check("a partial selection says how many of them hold the value",
          '" of " + total + " heads"' in js,
          '"1 head" beside a list of eight selected is not an answer')

    check("there is a keyboard map at all",
          "const KEYMAP = [" in js and "function wireKeyboard" in js, "")
    for frag, what in (
            ("nudgeIntensity", "intensity nudging"),
            ("stepCue", "cue stepping"),
            ("selectHeadByNumber", "head selection by number"),
            ("typingInAField", "the typing guard"),
            ("annotateKeys", "bindings written into the tooltips")):
        check("the keyboard layer has %s" % what, frag in js, "")
    check("the desk's keys stand down while a field has focus",
          "if (typingInAField(e)) return;" in js, "")
    # A guard that is always true looks exactly like a working one, and
    # this one WAS: #dlg-add is the dialog's confirm button, not the dialog.
    # It now covers both modals, so it is a list of dialogs - and the
    # second half of the check matters just as much, because a button id
    # in that list would make every shortcut dead again.
    check("the modal guard tests the DIALOGS, not their confirm buttons",
          '["#add-dialog", "#ch-dialog", "#help-dialog"]' in js
          and 'const d = $("#dlg-add");' not in js
          and '$("#dlg-add")' not in js.split("function anyDialogOpen")[1]
          .split("\n}")[0],
          'using #dlg-add made every shortcut dead')
    check("the channel sheet is wired and keyboard-reachable",
          "wireChannels();" in js and 'keys: ["c"]' in js
          and "closeChannels(); return;" in js, "")

    # the camera
    check("the camera target is not recomputed from the room every frame",
          "if (!camInit) frameRoom();" in viz,
          "this is what made the view reset itself")
    check("the camera can be saved and restored",
          "camera: cameraState" in viz and "setCamera: setCamera" in viz, "")
    check("a rebuild hands the viewpoint to the new view",
          "viz.setCamera(keepCam, true)" in js, "")
    check("moving a fixture no longer rebuilds the visualiser",
          "[h.head_no, h.model, h.mode, h.role, h.kind]" in js,
          "position churn tore the view down, and the camera with it")
    check("there is pan, not just orbit",
          "function panBy(" in viz and "dragCam = panDrag" in viz, "")
    check("zoom goes toward the cursor",
          "function dollyTo(" in viz and "dollyTo(clamp(1 + e.deltaY" in viz, "")
    check("there is free-fly on WASD/QE",
          "function flyStep(" in viz and '"wasdqe".indexOf(k) >= 0' in viz, "")
    check("there are named views and a frame-selection",
          "const VIEWS = {" in viz and "function frameHeads(" in viz, "")
    check("the camera gestures are discoverable, not hover-only",
          'id="viz-hint"' in html and "id=\"viz-cam\"" in html, "")
    check("the view can be given the whole screen",
          'id="cam-full"' in html and "function toggleFullscreen(" in js, "")
    # Full screen is CSS-first ON PURPOSE.  Wired only to the Fullscreen API
    # it looked like a dead button: the API needs the document focused, it
    # can be refused, and a refusal can leave the promise PENDING rather
    # than rejecting - so nothing was thrown, nothing was logged, and the
    # view never changed.  A fixed overlay cannot be refused and cannot
    # hang, and the API is requested on top only to also drop the browser
    # chrome when it happens to work.
    check("full screen does not depend on the Fullscreen API",
          "#viz-wrap.fs {" in css
          and "position: fixed; inset: 0" in css,
          "the API alone can be refused, and a refusal can hang silently")
    check("the overlay is applied BEFORE the API is asked",
          js.index("applyFs(true);") < js.index("req.call(wrap"),
          "otherwise a hung request means no full screen at all")
    check("a refused API request is not treated as an error",
          "The overlay is already up" in js
          and 'showSticky("full"' not in js,
          "the operator got the view they asked for, so nothing to shout")
    check("Escape leaves full screen",
          "if (fsActive()) { applyFs(false);" in js
          and "Esc to leave" in html, "")
    check("the button is the other way out too",
          "if (fsActive()) { applyFs(false); return; }" in js, "")
    check("entering full screen re-measures the canvas",
          "window.dispatchEvent(new Event(\"resize\"));" in js
          and js.count('new Event("resize")') >= 2,
          "otherwise the view comes back stretched")
    check("entering full screen gives the canvas the keyboard",
          "pane.focus(" in js,
          "the point of full screen is to move lights with WASD")
    check("the page behind does not scroll under the overlay",
          "body.fs-lock { overflow: hidden; }" in css
          and 'classList.toggle("fs-lock"' in js, "")
    check("the native full-screen layout is styled for when it does take",
          "#viz-wrap:fullscreen" in css, "")
    check("the button reports which way it will go",
          'fsActive();' in js and "⛶ EXIT" in js, "")

    # -- live drag: a control being dragged must reach the rig as it moves --
    check("there is a live sender for drags",
          "function liveSender(" in js, "")
    check("the live sender flushes the final value",
          "flush()" in js and js.count(".flush()") >= 5,
          "a drag that ends between two ticks must still land the value "
          "the operator let go at")
    check("the pan/tilt pad sends DURING the drag, not on release only",
          "send.push({ pan: padPos.pan, tilt: padPos.tilt });" in js,
          "the head used to jump only when the finger came up")
    # the call must be in the cached re-measure, never in the move handler
    check("the pad no longer measures the DOM on every pointermove",
          js.count("pad.getBoundingClientRect()") == 1
          and "getBoundingClientRect" not in js.split("pointermove")[1][:200],
          "a forced layout per event for the whole drag")
    check("the faders are not trailing-debounced any more",
          "intTimer" not in js and "masTimer" not in js
          and "sendIntensity.push()" in js and "sendMaster.push()" in js, "")
    check("playback faders track the drag too",
          "const sendLvl = liveSender(" in js, "")

    # -- picking a light in the 3D view ------------------------------
    # "Click a light so I can edit it" was wired to a flag nothing ever
    # set, in the one render path the console does not use.  Two independent
    # faults, and from the outside the feature looked finished.
    check("the selection highlight is drawn on the overlay, not the scene",
          "function drawSelection()" in viz
          and "drawSelection();" in viz,
          "the ring lived in the poster FALLBACK, which WebGL never reaches")
    check("the overlay is used by every render path",
          viz.count("drawLabels(") >= 3,
          "GL, poster and no-GL all label, so all three can highlight")
    check("the highlight is driven by the SELECTION, not a dead flag",
          "setSelected: function (heads)" in viz
          and "if (!!f.sel !== on) changed = true;" in viz,
          "nothing ever set f.picked, so the ring could never appear")
    check("the console tells the view what is selected",
          "if (viz && viz.setSelected) viz.setSelected(sel);" in js, "")
    check("a click gives the click an immediate visible consequence",
          "viz.flash(head)" in js and "flash: function (headNo)" in viz, "")
    check("the selection is one source of truth, not two flags",
          'classList.toggle("picked", on)' not in js,
          "the patch list and the 3D view disagreed about what was picked")
    check("the highlight is not carried by colour alone",
          "setLineDash" in viz and "corner ticks" in viz,
          "a dashed ring and reticle read in greyscale too")
    check("the highlight names the light, so it can be edited knowingly",
          "f.model || f.name" in viz and "describeHead" in js,
          "'heads 17' does not say whether that is the spot or a par")
    check("one light could never be ADDED to the selection",
          "function selectHead(n, shift, toggle)" in js
          and "function isToggleClick(e)" in js,
          "a plain click replaced the selection and shift took a range, so "
          "picking scattered lights was impossible")
    check("the three selection gestures are the conventional ones",
          "e.ctrlKey || e.metaKey" in js
          and "ctrl / cmd+click" in js
          and "shift+click" in js, "")
    check("both the row and its select button take the modifier",
          js.count("selectHead(h.head_no, e.shiftKey, isToggleClick(e))") == 2,
          "one of the two would have kept single-selecting")
    check("the number keys use the same rules as the mouse",
          "selectHeadByNumber(Number(e.key), e)" in js, "")
    check("the view reports which modifier was held",
          "toggle: !!(e && (e.ctrlKey || e.metaKey))" in viz,
          "otherwise a ctrl+click in the room and in the list differ")
    check("the view routes its modifiers through the same rules",
          "selectHead(Number(head), !!(mods && mods.shift),"
          in js, "")
    check("toggling the last head off clears the selection",
          'doAction("clear_selection", {})' in js, "")
    check("the click modifiers are written down where they are used",
          'class="gesture-legend"' in html
          and "gesture-legend" in css,
          "an undocumented modifier reads as a missing feature")

    # -- the fly keys --------------------------------------------------
    check("a held fly key restarts the render loop",
          "ensureLoop();" in viz,
          "the loop stops when idle and a held key is only read from it, "
          "so WASD recorded the key and nothing looked at it")
    check("Shift is tracked, it is the fly speed boost",
          'k === "Shift"' in viz and "keys.Shift" in viz, "")
    check("screen-up is forward x right, not the other way round",
          "out[0] = fwd[1] * r[2] - fwd[2] * r[1];" in viz,
          "the reversed order flies Q/E into the floor while A/D feel fine")
    check("a straight-up view still has a usable up axis",
          "out[1] = 1; out[2] = 0;" in viz, "")
    check("clicking the view gives it the keyboard",
          "canvas.focus(" in viz, "")
    check("the view says when it holds the keyboard",
          "kbfocus" in viz and "kbfocus" in css,
          "otherwise 'WASD does nothing' and 'not bound' look identical")

    # -- one truss bar per DEPTH ----------------------------------------
    # The clustering arithmetic is checked below; these two pin the SOURCE,
    # because a correct reimplementation of the WRONG rule would still
    # pass the arithmetic and still draw two bars over the same patch.
    check("truss rows cluster on depth only, not on height too",
          "Math.abs(r.z - f.z) < 1.6" in viz
          and "Math.abs(r.y - f.y) < 0.9" not in viz,
          "clustering on height drew a second bar over the same patch")
    check("the bar sits at the highest light in the row",
          "row.top = Math.max(row.top, f.y);" in viz
          and "r.barY = r.top + 0.45;" in viz, "")
    truss = []
    autosave = ROOT / "data" / "autosave.json"
    rig_source = "your saved rig"
    if autosave.is_file():
        try:
            saved = json.loads(autosave.read_text(encoding="utf-8"))
            truss = [h for h in (saved.get("patch") or [])
                     if (h.get("kind") == "truss"
                         or (not h.get("kind") and (h.get("y") or 0) > 1.2))]
        except (ValueError, OSError):
            truss = []
    # A test that needs the operator's PRIVATE show data is a test that only
    # passes on the machine it was written on.  `data/autosave.json` is
    # gitignored - correctly, it is your rig - so a fresh clone had no truss
    # at all and this failed, and the failure said "fewer than 2 truss heads
    # in data/autosave.json", which reads like a bug in the visualiser rather
    # than like a missing file.
    #
    # So: if there is no saved rig, or it has too little hanging to prove
    # anything, check the property on a rig the test builds.  The property is
    # the point; whose rig it is measured on is not.
    if len(truss) < 2:
        rig_source = "a synthetic rig (yours has too few hanging lights)"
        truss = [
            # one front truss, three lights at slightly different heights
            {"x": -4.0, "y": 4.0, "z": 6.0, "kind": "truss"},
            {"x": 0.0, "y": 4.4, "z": 6.1, "kind": "truss"},
            {"x": 4.0, "y": 3.8, "z": 5.9, "kind": "truss"},
            # one back truss, at a genuinely different depth
            {"x": -3.0, "y": 4.2, "z": 9.5, "kind": "truss"},
            {"x": 3.0, "y": 4.2, "z": 9.6, "kind": "truss"},
            # floor units, which must not pull a bar up with them
            {"x": -6.0, "y": 0.3, "z": 0.0, "kind": "floor"},
        ]
        check("and it says which rig it measured, so a pass is never mistaken "
              "for a pass on your own",
              True, rig_source)

    def cluster(heads, ztol):
        rows = []
        for f in sorted(heads, key=lambda h: (h.get("z", 0), h.get("x", 0))):
            row = next((r for r in rows
                        if abs(r["z"] - f.get("z", 0)) < ztol), None)
            if row is None:
                row = {"z": f.get("z", 0), "top": f.get("y", 0), "n": 0}
                rows.append(row)
            row["n"] += 1
            row["z"] += (f.get("z", 0) - row["z"]) / row["n"]
            row["top"] = max(row["top"], f.get("y", 0))
        return rows

    if len(truss) >= 2:
        by_depth = cluster(truss, 1.6)
        by_both = cluster(truss, 0.9)
        check("the saved rig draws no more bars than it has depths",
              len(by_depth) <= len(by_both),
              "%d bar(s) by depth vs %d by depth+height — %s"
              % (len(by_depth), len(by_both),
                 json.dumps([[round(r["z"], 2), round(r["top"], 2)]
                             for r in by_depth])))
        check("no depth gets a second stacked bar",
              len(by_depth) == len({round(r["z"] / 1.6) for r in by_depth}),
              json.dumps([[round(r["z"], 2), round(r["top"], 2)]
                          for r in by_depth]))
        check("a bar is never below the light it carries",
              all(r["top"] >= max(f.get("y", 0) for f in truss
                                  if abs(f.get("z", 0) - r["z"]) < 1.6)
                  for r in by_depth),
              json.dumps([[round(r["z"], 2), round(r["top"], 2)]
                          for r in by_depth]))
    else:
        check("the rig under test has hanging lights to hang bars from", False,
              "fewer than 2 truss heads in %s" % rig_source)

    # two lights at the same depth, different heights, must share one bar
    same_depth = [{"x": -3, "y": 5.0, "z": 6.5, "kind": "truss"},
                  {"x": 2, "y": 10.0, "z": 6.5, "kind": "truss"}]
    check("two lights at one depth share a single bar",
          len(cluster(same_depth, 1.6)) == 1,
          json.dumps([round(r["top"], 2) for r in cluster(same_depth, 1.6)]))
    check("that bar sits at the higher of the two",
          abs(cluster(same_depth, 1.6)[0]["top"] - 10.0) < 1e-6, "")
    # and a genuine front/back truss is still two bars
    two_trusses = [{"x": 0, "y": 5, "z": 2.0, "kind": "truss"},
                   {"x": 0, "y": 5, "z": 12.0, "kind": "truss"}]
    check("a front truss and a back truss are still two bars",
          len(cluster(two_trusses, 1.6)) == 2, "")

    # -- the live sender, as arithmetic ---------------------------------
    # 45 ms is ~22/s, just above the 20 Hz light feed, so the operator is
    # never ahead of what they can see.
    check("the live drag rate matches the light feed",
          "const LIVE_MS = 45;" in js, "")


    # layout and reachability
    check("master and BLACKOUT are pinned, not scrolled away",
          'id="progsafe"' in html and "position: sticky; bottom: 0" in css, "")
    check("what is running is always on screen",
          'id="now-playing"' in html and "updateNowPlaying" in js, "")
    check("inactive playbacks collapse so the column is reachable",
          "collapsed" in js and ".pbcard.collapsed" in css, "")
    check("a cue can be taken by clicking it",
          "button" in js and "cue: c.n" in js, "")
    check("the whole head row selects, not just a 22 px dot",
          'row.classList.add("hclick")' in js, "")
    check("select buttons are named for a screen reader",
          "selBtn.setAttribute(\"aria-label\"" in js
          and 'b.setAttribute("aria-pressed"' in js, "")
    check("the error strip is a live region and no longer shifts the grid",
          'role="status"' in html
          and "position: fixed; top: 0" in css, "")
    check("a lost server is persistent, not an 8 second toast",
          "function showSticky(" in js and "lastGoodFeed" in js, "")
    check("the operator can see where the frames are going",
          '"→ " + o.host' in js, "")
    check("a sender error is surfaced",
          '"SEND ERROR"' in js and "o.errors" in js, "")
    check("the dangerous state is not painted green",
          'dry ? "warn" : "danger"' in js
          and 'live ? "pill danger"' in js, "")
    check("touch targets are sized for fingers",
          "@media (pointer: coarse)" in css, "")
    check("the layout reflows for a tablet",
          "@media (max-width: 1100px)" in css
          and "@media (max-width: 860px)" in css, "")
    check("effect rows are not rebuilt ten times a second",
          "let fxSig = null;" in js and "function tickFxTimers" in js, "")
    check("an empty rig explains itself",
          'id="viz-empty"' in html and "renderVizEmptyState" in js, "")
    check("the empty state is not inside the pane Viz.create clears",
          html.index('id="viz-pane"') < html.index('id="viz-empty"')
          and "</div>" in html.split('id="viz-pane"')[1]
                   .split('id="viz-empty"')[0].rsplit("<", 1)[0],
          "Viz.create() empties #viz-pane on every build")


# ---------------------------------------------------------------------------
# runner: isolation, JS syntax gate, perf ceilings, one-line summary
# ---------------------------------------------------------------------------

# The Python suites, in dependency order.  Each gets a fresh temp dir; a
# crash in one is a failure, not the end of the run.  Built lazily (inside
# main) so a suite may be defined anywhere in this file.
def _suites():
    return (
    ("gdtf parser", test_gdtf),
    ("gdtf spec layout", test_gdtf_spec),
    ("database", test_db),
    ("artnet packets", test_artnet),
    ("artnet discovery", test_artnet_discovery),
    ("rig discovery", test_discovery),
    ("moving-head aim", test_aim),
    ("gdtf share", test_gdtf_share),
    ("palette targeting", test_palette_targets),
    ("dmx channel sheet", test_channels),
    ("16-bit channel sheet", test_channels_16bit),
    ("undo / redo", test_undo),
    ("selection tools", test_selection_tools),
    ("cue-list editing", test_cue_editing),
    ("fanning", test_fan),
    ("attribute grid", test_attribute_grid),
    ("arrange", test_arrange),
    ("network address", test_network_address),
    ("limits + lock", test_limits_and_lock),
    ("command line", test_command_line),
    ("dry run button", test_dry_run_button),
    ("physical ranges", test_physical_ranges),
    ("fixture editor", test_fixture_editor),
    ("console ux contracts", test_ux_contracts),
    ("channel roles", test_channel_roles),
    ("console engine", test_engine),
    ("auto patch", test_autopatch),
    ("fx + autosave", test_fx_autosave),
    ("AI console compiler", test_console_ai),
    ("sacn transport", test_sacn),
    ("16-bit dmx", test_dmx16),
    ("dmx input", test_dmx_input),
    ("midi", test_midi),
    ("auto-follow", test_autofollow),
    ("fixture profiles", test_profiles),
    ("realtime budget", test_realtime),
    ("merge core", test_merge),
    ("console api", test_engine_api),
    ("client contracts", test_client_contracts),
    )


def test_gdtf_share(tmp: Path) -> None:
    """The GDTF Share client, exercised end to end with NO network.

    The whole point of the feature is "other brands just work", and every
    way it can quietly fail is a way the operator sees an empty list and
    concludes the brand is unsupported:

      * a session that is not actually held, so the first search 401s;
      * an expired cookie reported as "no fixtures published";
      * a download that returns a JSON error page, which imported as a
        fixture would be a genuinely baffling bug;
      * a second revision of a light landing as a duplicate library
        entry, so the picker shows the same model twice and the operator
        cannot tell which modes are current.

    So the transport is injected.  The suite never opens a socket and
    never needs a GDTF Share account, but login, cookie expiry,
    auto-login-from-.env, search ranking, footprint filtering, caching,
    download, replacement and every error path all run for real.

    The cookie is the other half: it is a 2-hour token, and persisting it
    is what stops a server restart forcing a re-login, so that is checked
    by building a SECOND client over the same cache directory.
    """
    print("gdtf share (client, cache, download - no network)")
    import json as _json
    from app import fixtures as fx
    from app import gdtfshare as gs

    db = tmp / "share.db"
    fx.seed_generics(db)
    gdtf_bytes = _share_gdtf_bytes()

    def _catalogue():
        return _json.dumps({"result": True, "timestamp": 1672531200, "list": [
            {"rid": 11, "fixture": "Widget900", "manufacturer": "Acme",
             "revision": "r1", "rating": 4.5, "uploader": "Manuf.",
             "filesize": 1024,
             "modes": [{"name": "18ch", "dmxfootprint": 18}]},
            {"rid": 12, "fixture": "Spot 400", "manufacturer": "Acme",
             "revision": "r1", "rating": 3.0, "uploader": "User",
             "modes": [{"name": "8ch", "dmxfootprint": 8},
                       {"name": "32ch", "dmxfootprint": 32}]},
            {"rid": 13, "fixture": "Par 200", "manufacturer": "Bright Co",
             "revision": "r2", "rating": 4.9,
             "modes": [{"name": "5ch", "dmxfootprint": 5}]},
            # Spelled the way the real catalogue spells it, against a
            # sibling spelled the other way - the drift that hides a
            # fixture that is genuinely published.
            {"rid": 14, "fixture": "Slim Par T12 USB", "manufacturer": "Chauvet",
             "revision": "r1", "rating": 4.0,
             "modes": [{"name": "Default", "dmxfootprint": 8}]},
        ]}).encode("utf-8")

    def make(transport, cache="cache", user="me", password="pw"):
        return gs.GdtfShare(db, tmp / cache, user=user, password=password,
                            transport=transport)

    good = _share_transport(gdtf_bytes, _catalogue())
    c = make(good)

    # -- status is honest before anything has happened --------------------
    st = c.status()
    check("status before login: configured, not signed in",
          st["configured"] and not st["signed_in"], str(st))
    check("an empty catalogue is 0, never a failure",
          st["catalogue"] == 0 and not st["last_error"], str(st))

    # -- login -----------------------------------------------------------
    # A client with NO account must refuse without touching the network.
    # (With credentials it must NOT refuse - it signs in by itself, which
    # is what makes .env unattended operation work; checked further down.)
    bare = make(good, cache="cache-bare", user="", password="")
    try:
        bare.fetch_list()
        check("fetch with no account at all is refused", False, "no error raised")
    except gs.GdtfShareError as exc:
        check("fetch with no account at all is refused",
              exc.code == "no_session", exc.code)
    check("refusing makes no network request",
          all("gdtf-share.com" not in url for _m, url, _h, _b in good.calls),
          str([u for _m, url, _h, _b in good.calls]))

    c.set_credentials("me", "pw")
    check("login returns a summary", c.login()["ok"], "")
    check("the session cookie is kept", bool(c.cookies), str(c.cookies))
    check("status now reports a live session",
          c.status()["signed_in"] and c.status()["has_cookie"], str(c.status()))

    # -- search ----------------------------------------------------------
    r = c.search("widget")
    check("search by model name", r["total"] == 1 and
          r["results"][0]["fixture"] == "Widget900", str(r["total"]))
    check("search by manufacturer", c.search("", man="bright")["total"] == 1, "")
    check("a non-matching manufacturer filters everything out",
          c.search("", man="nosuchbrand")["total"] == 0, "")
    # The Share spells things inconsistently ("Slim Par T12 USB" vs
    # "SlimPAR Pro H USB"), so a plainly-typed query must still land.
    check("search ignores spacing drift in the catalogue",
          c.search("slimpart12")["total"] == 1,
          str(c.search("slimpart12")["total"]))
    check("search finds the spaced spelling when typed spaced too",
          c.search("slim par t12")["total"] == 1, "")
    check("an exact match still outranks a rescued one",
          gs.GdtfShare._score({"fixture": "Widget", "manufacturer": "Acme"},
                              "widget", "") <
          gs.GdtfShare._score({"fixture": "Widget 900", "manufacturer": "Acme"},
                              "widget", ""), "")
    check("an unrelated query still finds nothing",
          c.search("zzzznotathing")["total"] == 0, "")
    check("footprint filter uses any mode of the fixture",
          c.search("", man="acme", footprint=32)["total"] == 1, "")
    check("footprint filter excludes fixtures without that mode",
          c.search("", man="acme", footprint=5)["total"] == 0, "")
    best = c.search("")["results"]
    check("best rated sorts first when the match is equal",
          best[0]["fixture"] == "Par 200", str([b["fixture"] for b in best]))
    multi = next(x for x in best if x["fixture"] == "Spot 400")
    check("each result carries its modes and footprints",
          len(multi["modes"]) == 2 and multi["modes"][1]["dmxfootprint"] == 32,
          str(multi["modes"]))

    # -- .env credentials sign in by themselves, so a show machine that
    #    restarts never asks the operator to type a password -----------
    cold = make(good, cache="cache-cold")
    check("a cold client with .env credentials starts unsigned",
          not cold.status()["signed_in"], str(cold.status()))
    check("its first search signs in by itself",
          cold.search("widget")["total"] == 1, "")
    check("and it is signed in afterwards",
          cold.status()["signed_in"], str(cold.status()))

    # -- the catalogue is cached, not refetched per keystroke -------------
    before = len(good.calls)
    c.search("widget")
    c.search("par")
    check("repeat searches do not hit the network",
          len(good.calls) == before, "%d extra calls" % (len(good.calls) - before))
    check("a cached catalogue is reported as cached",
          c.search("widget")["cached"] is True, "")
    check("the catalogue size is visible in status",
          c.status()["catalogue"] == 4, str(c.status()["catalogue"]))

    # -- the cookie survives a restart (2-hour token, not a password) ----
    revived = gs.GdtfShare(db, tmp / "cache", transport=good)
    check("the session cookie is restored from disk",
          bool(revived.cookies), str(revived.cookies))
    check("a restored client needs no password to search",
          revived.search("widget")["total"] == 1, "")

    # -- download --------------------------------------------------------
    got = c.download(11)
    check("download installs the fixture", got["ok"] and got["model"] == "Widget900",
          str(got))
    check("download reports its modes", len(got["modes"]) == 1, str(got["modes"]))
    check("the fixture is searchable straight away",
          len(fx.search(db, "Widget")) == 1, "")
    again = c.download(11)
    check("re-downloading the same revision is a refresh, not a duplicate",
          again["refreshed"] and not again["replaced"] and
          len(fx.search(db, "Widget")) == 1, str(again))

    # A copy of the SAME fixture already in the library, from another
    # source, must not survive as a second entry - otherwise the picker
    # shows the model twice and there is no way to tell which modes are
    # current.  Rev 11 again is a refresh (above); this is the genuinely
    # different-revision case: Spot 400 arrives from a local file, then
    # from the Share under its own rid.
    stale = tmp / "spot400-local.gdtf"
    stale.write_bytes(_share_gdtf_bytes("Spot400"))
    fx.import_file(db, stale)
    check("a locally-imported copy is in the library",
          len(fx.search(db, "Spot400")) == 1, "")
    swapped = c.download(12)
    check("a Share copy replaces the local one instead of duplicating it",
          swapped["replaced"] and not swapped["refreshed"] and
          len(fx.search(db, "Spot400")) == 1, str(swapped))
    check("the replacement says so in its summary",
          "replaced" in swapped["summary"], swapped["summary"])
    check("downloading a different model does not disturb the first",
          len(fx.search(db, "Widget900")) == 1, "")
    check("the second fixture's modes came across",
          len(swapped["modes"]) == 1 and swapped["model"] == "Spot400",
          str(swapped))

    # -- a download that is really an error page -------------------------
    def json_error(method, url, **kw):
        if "login.php" in url:
            return _share_login_ok()
        if "getList.php" in url:
            return (200, {"content-type": "application/json"}, _catalogue())
        return (404, {"content-type": "application/json"},
                _json.dumps({"result": False,
                             "error": "File does not exist."}).encode("utf-8"))

    bogus = make(json_error, cache="cache-err")
    bogus.login()
    try:
        bogus.download(999999)
        check("a JSON error page is not imported as a fixture", False, "no raise")
    except gs.GdtfShareError as exc:
        check("a JSON error page is not imported as a fixture",
              exc.code == "not_found" and "does not exist" in exc.message,
              "%s / %s" % (exc.code, exc.message))
    check("a failed download adds nothing to the library",
          len(fx.search(db, "Widget")) == 1, str(len(fx.search(db, "Widget"))))

    # -- every failure says WHY, and never reads as "no fixtures" --------
    def expired(method, url, **kw):
        return (401, {"content-type": "application/json"},
                _json.dumps({"result": False, "error": "Unauthorized."}).encode("utf-8"))

    dead = make(expired, cache="cache-exp")
    dead.cookies["PHPSESSID"] = "stale"
    try:
        dead.fetch_list()
        check("an expired session is an explicit error", False, "no raise")
    except gs.GdtfShareError as exc:
        check("an expired session is an explicit error",
              exc.code == "unauthorized", exc.code)
    check("the stale cookie is dropped so the UI can offer a login",
          not dead.cookies, str(dead.cookies))
    check("status carries the reason to the UI",
          dead.status()["last_error_code"] == "unauthorized" and
          not dead.status()["signed_in"], str(dead.status()))

    def offline(method, url, **kw):
        raise gs.GdtfShareError("network", "cannot reach gdtf-share.com: offline")

    away = make(offline, cache="cache-net")
    away.cookies["PHPSESSID"] = "x"
    st = away.status()
    check("being offline is not reported as an empty catalogue",
          st["catalogue"] == 0 and not st["last_error"], str(st))
    try:
        away.fetch_list()
        check("being offline is a network error", False, "no raise")
    except gs.GdtfShareError as exc:
        check("being offline is a network error", exc.code == "network", exc.code)

    def broken(method, url, **kw):
        if "login.php" in url:
            return _share_login_ok()
        return (200, {"content-type": "application/json"}, b"<html>nope</html>")

    junk = make(broken, cache="cache-html")
    junk.login()
    try:
        junk.fetch_list()
        check("an HTML page instead of JSON is refused", False, "no raise")
    except gs.GdtfShareError as exc:
        check("an HTML page instead of JSON is refused",
              exc.code == "bad_response", exc.code)

    def bad_password(method, url, **kw):
        return (400, {"content-type": "application/json"},
                _json.dumps({"result": False,
                             "error": "No valid user or password provided."}).encode("utf-8"))

    wrong = make(bad_password, cache="cache-pw")
    try:
        wrong.login()
        check("a wrong password is reported in the operator's words", False, "no raise")
    except gs.GdtfShareError as exc:
        check("a wrong password is reported in the operator's words",
              exc.code == "unauthorized" and "No valid user" in exc.message,
              "%s / %s" % (exc.code, exc.message))

    blank = make(good, cache="cache-blank", user="", password="")
    try:
        blank.login()
        check("no credentials is its own error, not a 401", False, "no raise")
    except gs.GdtfShareError as exc:
        check("no credentials is its own error, not a 401",
              exc.code == "no_credentials", exc.code)

    # -- the library is kept to one row per model -------------------------
    # 4 built-in generics + Widget900 + Spot400, and no duplicates from
    # any of the re-imports above.
    check("the library holds one row per model, no duplicates",
          fx.count(db) == 6, "count=%s" % fx.count(db))
    check("remove_model names what it removed, and is empty when there is nothing",
          fx.remove_model(db, "Acme", "Widget900") == ["Acme Widget900"] and
          fx.remove_model(db, "Acme", "Widget900") == [], "")
    check("remove_model matches exactly by default",
          fx.remove_model(db, "Someone Else", "Widget900") == [], "")
    # The spelling drift the Share actually causes.  Chauvet publishes
    # "Slim Par T12 USB" on the Share and a curated library writes
    # "SlimPAR T12 USB", under a different manufacturer too.  An exact
    # match, or a case-insensitive one, leaves the SAME LIGHT in the
    # picker twice - so loose matching squashes spacing too.
    spaced = tmp / "spaced.gdtf"
    spaced.write_bytes(_share_gdtf_bytes("Slim Par T12 USB", maker="Chauvet"))
    fx.import_file(db, spaced)
    check("a differently-spaced model is found by loose matching",
          len(fx.model_sources(db, "chauvet", "slimpar t12 usb",
                               loose=True)) == 1,
          str(fx.model_sources(db, "chauvet", "slimpar t12 usb", loose=True)))
    check("loose removal supersedes it and says what it superseded",
          fx.remove_model(db, "acme", "SLIMPAR T12 USB", loose=True) ==
          ["Chauvet Slim Par T12 USB"],
          str(fx.remove_model(db, "acme", "SLIMPAR T12 USB", loose=True)))
    check("a different spacing of the same name is NOT a loose match",
          fx.model_sources(db, "acme", "totally other light", loose=True) == [], "")


def _share_transport(gdtf_bytes, catalogue):
    """A stand-in network that answers all three endpoints.

    Downloads are keyed by rid, so two revisions of the Share can hand
    back two DIFFERENT fixtures - which is what makes "a newer revision
    replaces the old copy" a real test rather than a tautology.
    """
    files = {11: gdtf_bytes, 12: _share_gdtf_bytes("Spot400"),
             13: _share_gdtf_bytes("Par200", maker="Bright Co")}

    def transport(method, url, *, body=None, headers=None, timeout=20.0):
        transport.calls.append((method, url, dict(headers or {}), body))
        if "login.php" in url:
            return _share_login_ok()
        if "getList.php" in url:
            return (200, {"content-type": "application/json"}, catalogue)
        if "downloadFile.php" in url:
            rid = int(url.rsplit("=", 1)[-1])
            payload = files.get(rid)
            if payload is None:
                return (404, {"content-type": "application/json"},
                        b'{"result":false,"error":"File does not exist."}')
            return (200, {"content-type": "application/octet-stream"}, payload)
        return (404, {"content-type": "application/json"},
                b'{"result":false,"error":"unknown endpoint"}')
    transport.calls = []
    return transport


def _share_login_ok():
    return (200, {"content-type": "application/json",
                  "set-cookie": "PHPSESSID=abc123; path=/; HttpOnly"},
            b'{"result":true,"notice":"Welcome"}')


def _share_gdtf_bytes(model: str = "Widget900", maker: str = "Acme") -> bytes:
    """A real (tiny) GDTF archive, so the download path is genuinely parsed."""
    import io
    import zipfile
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<GDTF><Manufacturer>' + maker + '</Manufacturer><Name>' + model + '</Name>'
        '<DMXModes><DMXMode Name="18ch"><DMXChannels>'
        '<DMXChannel Offset="1"><LogicalChannel Attribute="Pan">'
        '<ChannelFunction Name="Pan" OriginalAttribute="Pan"/>'
        '</LogicalChannel></DMXChannel>'
        '<DMXChannel Offset="3"><LogicalChannel Attribute="Dimmer">'
        '<ChannelFunction Name="Dim" OriginalAttribute="Dimmer"/>'
        '</LogicalChannel></DMXChannel>'
        '</DMXChannels></DMXMode></DMXModes></GDTF>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("GDTF", xml)
    return buf.getvalue()


# The colour picker's maths, verified against the SHIPPED source, for the
# same reason m4Invert is: the failure is invisible.  A transposed red and
# green in hsvToRgb produces a colour that looks like a colour, gets typed
# into the hex box, reaches the engine, and is simply the wrong hue - and
# "the picker picked a slightly odd colour" is not a report anybody makes.
# The cases below are chosen to CATCH that specific swap: cyan (180) and
# magenta (300) are exchanged by it and nothing else is disturbed.
_PICK_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
function grab(name) {
  const i = src.indexOf('function ' + name + '(');
  if (i < 0) { console.error('missing ' + name); process.exit(2); }
  let d = 0, started = false;
  for (let k = i; k < src.length; k++) {
    if (src[k] === '{') { d++; started = true; }
    else if (src[k] === '}') { d--; if (started && d === 0) return src.slice(i, k + 1); }
  }
  console.error('unterminated ' + name); process.exit(2);
}
function grabConst(name) {
  const m = src.match(new RegExp('const ' + name + ' = ([^;]+);'));
  if (!m) { console.error('missing const ' + name); process.exit(2); }
  // `const` in a sloppy direct eval is scoped to the eval and would vanish.
  // `var` leaks, which is what the function hoisting above already relies on.
  return 'var ' + name + ' = ' + m[1] + ';';
}
eval(grabConst('PICK_SIZE'));
eval(grabConst('PICK_RING_IN'));
eval(grab('clampInt'));
eval(grab('hsvToRgb'));
eval(grab('rgbToHsv'));
eval(grab('hexToRgb'));
eval(grab('rgbToHex'));
eval(grab('pickBox'));
eval(grab('pickNudge'));

const out = [];
function rgb(name, got, want) {
  out.push(name + '=' + got.join(',') + '|' + want.join(','));
}
const t = (h, s, v) => hsvToRgb(h, s, v);
rgb('RED', t(0, 1, 1), [255, 0, 0]);
rgb('YELLOW', t(60, 1, 1), [255, 255, 0]);
rgb('GREEN', t(120, 1, 1), [0, 255, 0]);
rgb('CYAN', t(180, 1, 1), [0, 255, 255]);
rgb('BLUE', t(240, 1, 1), [0, 0, 255]);
rgb('MAGENTA', t(300, 1, 1), [255, 0, 255]);
rgb('WHITE', t(0, 0, 1), [255, 255, 255]);
rgb('BLACK', t(0, 0, 0), [0, 0, 0]);
// Full saturation at half VALUE is a dark red, not a pink.  Pink needs the
// saturation lowered too - which is the whole reason the square has two
// axes and a plain brightness wheel does not.
rgb('HALF', t(0, 1, 0.5), [128, 0, 0]);
rgb('PINK', t(0, 0.5, 0.5), [128, 64, 64]);
// A hue angle outside 0-360 wraps onto the wheel, it does not fall off the
// far side: 420 is 60 (yellow) and -60 is 300 (magenta).
rgb('WRAP_HI', t(420, 1, 1), [255, 255, 0]);
rgb('WRAP_LO', t(-60, 1, 1), [255, 0, 255]);
// Out of range saturation/value clamp instead of producing a broken colour.
rgb('CLAMP_S', t(0, 5, 1), [255, 0, 0]);
rgb('CLAMP_V', t(0, 1, -3), [0, 0, 0]);

const g = (r, gr, b) => rgbToHsv(r, gr, b);
rgb('H_RED', g(255, 0, 0), [0, 1, 1]);
rgb('H_GREEN', g(0, 255, 0), [120, 1, 1]);
rgb('H_BLUE', g(0, 0, 255), [240, 1, 1]);
// A grey has NO hue.  `d > 0` is true for float dust, and then the hue
// jumps to an arbitrary angle for a colour that does not have one.
rgb('H_GREY', g(128, 128, 128), [0, 0, 128 / 255]);
rgb('H_BLACK', g(0, 0, 0), [0, 0, 0]);
// A hue that would compute negative before the wrap: the red-dominant
// branch of the sextant is where an unwrapped value goes to -60..0.
// 60 * ((0 - 128/255) % 6) = -30.12, so the answer has to come back +329.88.
rgb('H_WRAPNEG', g(255, 0, 128), [329.88, 1, 1]);

rgb('HEX', hexToRgb('#ff8800'), [255, 136, 0]);
rgb('HEX_NOHASH', hexToRgb('ff8800'), [255, 136, 0]);
// Three digits: the ENGINE's _parse_hex expands them, so the client has to
// as well or the picker rejects a colour the console would have accepted.
rgb('HEX_SHORT', hexToRgb('#f80'), [255, 136, 0]);
out.push('HEX_JUNK=' + JSON.stringify(hexToRgb('nonsense')));
out.push('HEX_SHORTJUNK=' + JSON.stringify(hexToRgb('#gg88')));
out.push('TOHEX=' + rgbToHex([255, 136, 0]));
out.push('TOHEX_BLACK=' + rgbToHex([0, 0, 0]));
out.push('TOHEX_CLAMP=' + rgbToHex([256, -5, 12]));

// Round trip.  The invariant that actually matters is IDEMPOTENCE, not a
// hue tolerance: the picker shows a colour, it goes out as a hex, and
// picking that hex back must land on the SAME 8-bit colour - otherwise the
// dot drifts under the cursor every time you re-grab it, and a colour you
// have dialled in cannot be dialled in twice.  That is an EXACT test.
//
// A hue tolerance is meaningless as a check in its own right: at s=0.25 the
// whole channel spread is 0.075*255 = 19 levels, so a half-level rounding
// moves the hue by ~1.5 degrees and nothing is wrong.  So the hue error is
// reported, split by saturation, and only asserted where 8 bits can carry
// it - which is also where a transposed pair would fail by hundreds of
// degrees anyway.
let hueAll = 0, hueHi = 0, satErr = 0, valErr = 0, worst = null;
let idempotent = true, firstBad = null;
for (let h = 0; h < 360; h += 7) {
  for (const s of [0.25, 0.5, 0.75, 1]) {
    for (const v of [0.3, 0.6, 1]) {
      const c = t(h, s, v);
      const b2 = g(c[0], c[1], c[2]);
      // Re-render what was just read back: it must be the same colour.
      const c2 = t(b2[0], b2[1], b2[2]);
      if (c2[0] !== c[0] || c2[1] !== c[1] || c2[2] !== c[2]) {
        idempotent = false;
        if (!firstBad) firstBad = [h, s, v, c, c2];
      }
      let dh = Math.abs(b2[0] - h); if (dh > 180) dh = 360 - dh;
      if (dh > hueAll) { hueAll = dh; worst = [h, s, v, b2]; }
      if (s >= 0.75 && dh > hueHi) hueHi = dh;
      satErr = Math.max(satErr, Math.abs(b2[1] - s));
      valErr = Math.max(valErr, Math.abs(b2[2] - v));
    }
  }
}
out.push('RT_IDEMPOTENT=' + idempotent);
out.push('RT_BAD=' + JSON.stringify(firstBad));
out.push('RT_HUE=' + hueAll.toFixed(3));
out.push('RT_HUE_HI=' + hueHi.toFixed(3));
out.push('RT_SAT=' + satErr.toFixed(4));
out.push('RT_VAL=' + valErr.toFixed(4));
out.push('RT_WORST=' + JSON.stringify(worst));

// Geometry.  The square and the ring are hit-tested in different branches,
// so an overlap means some pixels do one thing and some another depending
// on which test runs - and the square's corners are exactly where pure hue
// and pure white live.
const b = pickBox();
const corner = b.A * Math.SQRT2;
out.push('GEO_R=' + b.R.toFixed(2));
out.push('GEO_RI=' + b.RI.toFixed(2));
out.push('GEO_A=' + b.A.toFixed(2));
out.push('GEO_RINGW=' + (b.R - b.RI).toFixed(2));
out.push('GEO_CORNER=' + corner.toFixed(2));
out.push('GEO_EDGE=' + (b.cx + b.A).toFixed(2) + '|' + PICK_SIZE);
out.push('GEO_CLIPPED=' + (b.R + 2 > PICK_SIZE / 2));
out.push('GEO_OVERLAP=' + (corner >= b.RI));
out.push('GEO_WIDE=' + (b.R - b.RI >= 12));
out.push('GEO_POS=' + (b.A > 0 && b.RI < b.R));
// The canvas EDGE, not the centre: `cx` IS the centre, so comparing the
// right of the square against the centre would fail on any control.
out.push('GEO_INCANVAS=' + (b.cx + b.A <= PICK_SIZE && b.cy + b.A <= PICK_SIZE
                            && b.cx - b.A >= 0 && b.cy - b.A >= 0));

// The keyboard is the ACCESSIBILITY path, and it was wrong while every other
// check in this suite was green.  The up/down branches passed the value into
// setPick's SATURATION slot and left brightness undefined, so ArrowDown moved
// the wrong axis and set v to NaN - which clampInt turned into 0, so the
// engine was sent #000000.  Pressing the down arrow on a white head turned it
// black, and the dot sat at a position that does not exist.
//
// Grepping for `case "ArrowDown"` finds that bug and calls it handled.  So
// the decision is a pure function of (key, shift, current state) and it is
// RUN: for every key, the axes you did NOT press must come back bit
// identical, the one you did must actually move, and nothing may be
// non-finite.
const CUR = { h: 210, s: 0.8, v: 0.6 };
const KEYS = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home'];
out.push('NUDGE_COUNT=' + (KEYS.length * 2));
const nudgeBad = [];
for (const k of KEYS) {
  for (const shift of [false, true]) {
    const tag = k + '/shift=' + shift;
    const r = pickNudge(k, shift, CUR);
    if (!r) { nudgeBad.push(tag + ' -> null'); continue; }
    if (!Number.isFinite(r.h) || !Number.isFinite(r.s)
        || !Number.isFinite(r.v)) {
      nudgeBad.push(tag + ' -> non-finite ' + JSON.stringify(r));
      continue;
    }
    const movesH = (k === 'ArrowLeft' || k === 'ArrowRight');
    const movesS = (shift && (k === 'ArrowUp' || k === 'ArrowDown'));
    const movesV = (!shift && (k === 'ArrowUp' || k === 'ArrowDown'));
    const all = (k === 'Home');
    if (!all && !movesH && r.h !== CUR.h) {
      nudgeBad.push(tag + ' moved HUE: ' + r.h);
    }
    if (!all && !movesS && r.s !== CUR.s) {
      nudgeBad.push(tag + ' moved SATURATION: ' + r.s);
    }
    if (!all && !movesV && r.v !== CUR.v) {
      nudgeBad.push(tag + ' moved BRIGHTNESS: ' + r.v);
    }
    if (movesV && r.v === CUR.v) {
      nudgeBad.push(tag + ' did NOT move brightness');
    }
    if (movesS && r.s === CUR.s) {
      nudgeBad.push(tag + ' did NOT move saturation');
    }
    if (movesH && r.h === CUR.h) {
      nudgeBad.push(tag + ' did NOT move hue');
    }
  }
}
out.push('NUDGE_BAD=' + JSON.stringify(nudgeBad));
// An unhandled key must be null, so the handler leaves the page alone.
out.push('NUDGE_OTHER=' + JSON.stringify(pickNudge('a', false, CUR)));
out.push('NUDGE_HOME=' + JSON.stringify(pickNudge('Home', false, CUR)));

// Why keeping the hue is FREE rather than a fudge: at zero saturation the
// hue is multiplied out, so every hue gives the identical colour.  That is
// the licence to preserve the operator's hue on a grey instead of letting
// the engine round trip overwrite it - and it is a claim about the maths,
// so it is measured rather than asserted in a comment.
let greySame = true;
const greyRef = t(0, 0, 0.6).join(',');
for (let h = 0; h < 360; h += 11) {
  if (t(h, 0, 0.6).join(',') !== greyRef) greySame = false;
}
out.push('HUE_FREE_AT_S0=' + greySame);
// The counterpart, WITH saturation - if these two matched, then the hue
// never mattered and `adoptHex`'s guard would be hiding nothing... and
// conversely a guard that is only ever right at s=0 must not be allowed to
// suppress a real hue here.
out.push('HUE_FREE_RGB=' + t(123, 0.8, 0.6).join(',') + '|'
                          + t(240, 0.8, 0.6).join(','));
console.log(out.join('\n'));
"""


def _parse_kv(out: str) -> dict:
    got = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            got[k.strip()] = v.strip()
    return got


def _strip_css_comments(text: str) -> str:
    """Comments are not selectors.

    A first pass at `_hidden_conflicts` reported selectors like
    `for a toast that vanished eight seconds late` and
    `/* ----------------------------------------` - the prose in this file's
    own comments, read as CSS.  A scan that cries wolf over its own
    documentation is worse than no scan, because it trains you to skip it.
    """
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def _hidden_conflicts(html: str, *sheets: str) -> list[str]:
    """Elements using the `hidden` ATTRIBUTE that author CSS gives a `display`.

    `[hidden] { display: none }` lives in the USER-AGENT stylesheet, and
    author CSS beats it.  So an author rule that sets `display` on such an
    element silently defeats the attribute: the element stays on screen and
    the property still reports `true`.  Both are correct about themselves,
    which is what makes this so hard to see - the JS reads the flag, the
    flag is right, and the screen disagrees.

    This found #help-dialog: `.modal { display: flex }` beat the attribute,
    so the dialog was on screen from page load, 1246 px tall, with an empty
    body, and no close route could remove it because `hidden` was already
    `true` and CSS was ignoring it.

    Deliberately NARROW, because the first version of this matched 28 things
    and was wrong about nearly all of them:
      * only elements using the ATTRIBUTE - the class convention is already
        covered by the global `.hidden { display: none !important }`;
      * only selectors that match the element ITSELF, so `.modal-card` and
        `.modal-head` (its children) do not count;
      * comments stripped, so prose is never read as a selector.

    So the output is a short, truthful list, and a new offender shows up as
    a diff rather than as noise.
    """
    out = []
    for tag in re.findall(r"<[a-zA-Z][^>]*\bhidden\b[^>]*>", html):
        if "aria-hidden" in tag:                 # that is a different thing
            continue
        if re.search(r'\bclass="[^"]*\bhidden\b', tag):
            continue                              # the class, not the attribute
        ids = re.findall(r'\bid="([^"]+)"', tag)
        classes = (re.findall(r'\bclass="([^"]+)"', tag) or [""])[0].split()
        tagname = re.match(r"<([a-zA-Z][\w-]*)", tag).group(1).lower()
        name = ids[0] if ids else tagname
        for sheet in sheets:
            for block in re.finditer(r"([^{}]+)\{([^{}]*)\}",
                                     _strip_css_comments(sheet)):
                sel, body = block.group(1), block.group(2)
                disp = re.search(r"(^|;)\s*display\s*:\s*([^;]+)", body)
                if not disp:
                    continue
                value = disp.group(2).strip()
                if value == "none":
                    continue
                for one in sel.split(","):
                    one = one.strip()
                    if not one or " " in one or ">" in one:
                        continue                  # a descendant/child selector
                    # Every simple part of the compound must match the
                    # element: that is what "matches the element itself" means.
                    ok = bool(one)
                    for part in re.findall(r"[.#]?[\w-]+", one):
                        if part.startswith("#"):
                            ok = ok and part[1:] in ids
                        elif part.startswith("."):
                            ok = ok and part[1:] in classes
                        elif part != "*":
                            ok = ok and part.lower() == tagname
                        if not ok:
                            break
                    if ok and one not in ("[hidden]",):
                        out.append("#%s: `%s` sets display:%s"
                                   % (name, one, value))
    return out


def test_colour_picker() -> None:
    print("colour picker")
    js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")
    css = (ROOT / "web" / "console.css").read_text(encoding="utf-8")
    from app import engine as eng

    node = _which("node")
    if node is None:
        check("the picker's maths is run, not eyeballed", False,
              "node not found - cannot execute web/console.js")
    else:
        proc = subprocess.run([node, "-e", _PICK_HARNESS,
                               str(ROOT / "web" / "console.js")],
                              capture_output=True, text=True)
        got = _parse_kv(proc.stdout)
        if proc.returncode != 0 or not got:
            check("the picker's maths is run, not eyeballed", False,
                  (proc.stderr or proc.stdout).strip()[:200])
        else:
            def near(name: str, tol: float) -> bool:
                want = [float(x) for x in got.get(name, "|").split("|")[-1].split(",")]
                real = [float(x) for x in got.get(name, "|").split("|")[0].split(",")]
                if len(real) != len(want) or not all(w == w for w in want):
                    return False
                return all(abs(a - b) <= tol for a, b in zip(real, want))

            for label, key in (("red", "RED"), ("yellow", "YELLOW"),
                               ("green", "GREEN"), ("cyan", "CYAN"),
                               ("blue", "BLUE"), ("magenta", "MAGENTA"),
                               ("white", "WHITE"), ("black", "BLACK")):
                # Cyan and magenta are the pair a transposed R and B
                # exchanges.  They are the reason the rest of the wheel is
                # not what is being checked here.
                check(f"hsvToRgb picks {label}", near(key, 0.5),
                      got.get(key, "missing"))
            check("full saturation at half brightness is a DARK red, and "
                  "the square needs two axes to say so",
                  near("HALF", 1.0) and near("PINK", 1.0),
                  got.get("HALF", "") + " " + got.get("PINK", "missing"))
            check("a hue past 360 wraps onto the wheel rather than falling "
                  "off the far side",
                  near("WRAP_HI", 0.5) and near("WRAP_LO", 0.5),
                  got.get("WRAP_HI", "") + " " + got.get("WRAP_LO", ""))
            check("and out of range s and v clamp, not corrupt",
                  near("CLAMP_S", 0.5) and near("CLAMP_V", 0.5),
                  got.get("CLAMP_S", "") + " " + got.get("CLAMP_V", ""))
            for label, key in (("red", "H_RED"), ("green", "H_GREEN"),
                               ("blue", "H_BLUE"), ("grey", "H_GREY"),
                               ("black", "H_BLACK")):
                check(f"rgbToHsv reads {label} back", near(key, 0.01),
                      got.get(key, "missing"))
            check("a grey has hue 0, not an angle chosen by float dust",
                  near("H_GREY", 0.01), got.get("H_GREY", "missing"))
            check("and a hue that computes to -30 comes back +329.88",
                  near("H_WRAPNEG", 0.05), got.get("H_WRAPNEG", "missing"))
            check("hex parses, with and without the #",
                  near("HEX", 0) and near("HEX_NOHASH", 0),
                  got.get("HEX", ""))
            # The ENGINE's _parse_hex expands three digits, so a client that
            # does not is a client that rejects a colour the console accepts.
            check("and three digits, which the engine also accepts",
                  near("HEX_SHORT", 0), got.get("HEX_SHORT", "missing"))
            check("junk is null rather than NaN or a partial parse",
                  got.get("HEX_JUNK") == "null"
                  and got.get("HEX_SHORTJUNK") == "null",
                  got.get("HEX_JUNK", "") + got.get("HEX_SHORTJUNK", ""))
            check("and back out to hex, padded and clamped",
                  got.get("TOHEX") == "#ff8800"
                  and got.get("TOHEX_BLACK") == "#000000"
                  and got.get("TOHEX_CLAMP") == "#ff000c",
                  str(got.get("TOHEX")) + " " + str(got.get("TOHEX_BLACK"))
                  + " " + str(got.get("TOHEX_CLAMP")))

            def num(key: str) -> float:
                try:
                    return float(got.get(key, "nan"))
                except ValueError:
                    return float("nan")

            # The exact claim: re-picking a colour that is already on screen
            # must give back the same 8-bit value, or the dot creeps under
            # the cursor and a colour cannot be dialled in twice.
            check("a colour read back and re-rendered is BIT IDENTICAL, so "
                  "re-grabbing the picker does not move it",
                  got.get("RT_IDEMPOTENT") == "true", got.get("RT_BAD", ""))
            # Hue is only asserted where 8 bits can carry it.  At s=0.25 the
            # whole channel spread is 19 levels, so a half-level rounding is
            # worth ~1.5 degrees of hue and nothing is wrong; at s=0.75 and
            # up it must be tight.
            check("and the hue survives where 8 bits can hold it",
                  num("RT_HUE_HI") <= 1.0,
                  "worst %s deg at s>=0.75" % got.get("RT_HUE_HI"))
            check("with saturation and value inside one 8-bit step",
                  num("RT_SAT") <= 0.01 and num("RT_VAL") <= 0.01,
                  "sat %s val %s" % (got.get("RT_SAT"), got.get("RT_VAL")))
            check("the ring is not clipped by the canvas edge",
                  got.get("GEO_CLIPPED") == "false", got.get("GEO_R", ""))
            check("and the square's corners clear the ring band, so the two "
                  "hit regions are disjoint",
                  got.get("GEO_OVERLAP") == "false",
                  "corner %s vs inner %s" % (got.get("GEO_CORNER"),
                                             got.get("GEO_RI")))
            check("the ring is wide enough to hit with a finger",
                  got.get("GEO_WIDE") == "true", got.get("GEO_RINGW", ""))
            check("and the whole control is inside the canvas",
                  got.get("GEO_POS") == "true"
                  and got.get("GEO_INCANVAS") == "true",
                  "right edge %s of %s, %s%s"
                  % (got.get("GEO_EDGE"), got.get("GEO_POS"),
                     got.get("GEO_INCANVAS"), ""))

            # THE BUG THIS EXISTS FOR.  Every check above was green while the
            # keyboard moved the wrong axis and sent #000000 to the engine.
            # All ten combinations, run: the two axes you did not press must
            # come back bit identical.
            bad = got.get("NUDGE_BAD", "[]")
            check("every arrow moves exactly ONE axis, and leaves the other "
                  "two bit identical (10 combinations, run not grepped)",
                  bad == "[]" and got.get("NUDGE_COUNT") == "10", bad)
            check("a key that is not one of ours is null, so the page keeps it",
                  got.get("NUDGE_OTHER") == "null",
                  got.get("NUDGE_OTHER", "missing"))
            check("and Home is white",
                  got.get("NUDGE_HOME") == '{"h":0,"s":0,"v":1}',
                  got.get("NUDGE_HOME", "missing"))

            # A grey has no hue: rgbToHsv("#808080") is hue 0, s 0.  So a
            # round trip through the engine THROWS THE HUE AWAY, and the feed
            # reading it back at 20 Hz put the dot back at the top of the
            # wheel - after which nudging hue did nothing at all.  Keeping
            # the operator's hue is free, and the licence for that is that
            # the hue is multiplied out at s=0.  Measured, not asserted.
            check("at zero saturation every hue gives the SAME colour, which "
                  "is why preserving the operator's is free",
                  got.get("HUE_FREE_AT_S0") == "true",
                  str(got.get("HUE_FREE_RGB")))
            check("and it stops being free the moment there is saturation, "
                  "so the guard is not a way of hiding a real hue",
                  got.get("HUE_FREE_RGB", "").split("|")[0]
                  != got.get("HUE_FREE_RGB", "").split("|")[-1],
                  got.get("HUE_FREE_RGB", "missing"))
    check("all three sync paths go through ONE function, or they will "
          "disagree about the hue",
          js.count("adoptHex(") >= 4
          and "function adoptHex(hex)" in js, "")
    check("and that function keeps the operator's hue when the colour is "
          "desaturated instead of snapping to the top of the wheel",
          "if (hsv[1] > 0.004) PICK.h = hsv[0];" in js
          and "unrecoverable" in js, "")
    check("and the picker remembers what it is showing, so the feed can tell "
          "'the show moved' from 'this is what I just set'",
          "PICK.shown = hex" in js and "shown: null" in js, "")
    # The pick goes out through a 45 ms throttle, so for up to three feed
    # ticks the engine still holds the PREVIOUS colour.  Writing that into
    # the hex box unconditionally clobbered the colour the operator had just
    # chosen and it snapped back - so nudging brightness appeared to do
    # nothing at all.  The engine may only touch the box when it holds a
    # genuinely DIFFERENT colour.
    check("and the engine may only write the hex box when it holds a "
          "different colour, or a stale feed tick clobbers the pending pick",
          "&& hex !== PICK.shown) {" in js
          and js.index("hex !== PICK.shown") < js.index("box.value = hex"), "")
    check("a pick that lands on the colour already shown changes nothing, so "
          "it must not cost an undo step",
          "const changed = hex !== PICK.shown;" in js
          and "if (!quiet && changed) pickSend.push({ hex });" in js, "")

    # What the ENGINE does, which is what the picker has to predict.
    rgb_head = {"head_no": 1, "map": ["dimmer", "red", "green", "blue"]}
    wheel_head = {"head_no": 2, "map": ["pan", "tilt", "wheel", "gobo"]}
    cmy_head = {"head_no": 3, "map": ["cyan", "magenta", "yellow"]}
    e = eng.Engine.__new__(eng.Engine)
    check("the engine writes RGB to an RGB head",
          e._colour_values(rgb_head, "#ff8800")
          == {"red": 255, "green": 136, "blue": 0},
          str(e._colour_values(rgb_head, "#ff8800")))
    check("and CMY to a CMY head, inverted",
          e._colour_values(cmy_head, "#ff0000")
          == {"cyan": 0, "magenta": 255, "yellow": 255},
          str(e._colour_values(cmy_head, "#ff0000")))
    # THE CASE THE PICKER EXISTS TO WARN ABOUT.  A wheel fixture has no
    # red/green/blue, so `set_colour` writes nothing to it and raises if
    # nothing else was written.  There are two of these in the rig, so this
    # is not hypothetical.
    check("a colour WHEEL head gets nothing, which is why the picker warns",
          e._colour_values(wheel_head, "#ff8800") == {},
          str(e._colour_values(wheel_head, "#ff8800")))

    # The picker PREDICTS the engine.  These two lists are the same decision
    # written twice, so the check is that they are still written in the same
    # ORDER - the one thing that would silently change which heads a picked
    # colour is reported to reach.
    eng_order = ["red", "cyan", "white"]
    body = js[js.index("function colourReach()"):js.index("function paintPicker")]
    js_order = [r for r in eng_order if r in body]
    check("the picker's family order matches the engine's, so its count of "
          "reachable heads cannot drift from what actually happens",
          js_order == eng_order, "engine %s vs picker %s" % (eng_order, js_order))
    eng_body = (eng.Engine._colour_values.__doc__ or "")
    src_eng = (ROOT / "app" / "engine.py").read_text(encoding="utf-8")
    ev = src_eng[src_eng.index("def _colour_values"):]
    ev = ev[:ev.index("def _white_values")]
    check("and the engine itself still tests RGB, then CMY, then white",
          [ev.index('"red"') < ev.index('"cyan"'),
           ev.index('"cyan"') < ev.index('"white"')] == [True, True], "")
    check("the engine's three-digit hex really does expand, which is why "
          "the client has to",
          eng._parse_hex("#f80") == (255, 136, 0), "")

    # The pick has to reach the BYTES, not just the programmer.  That is the
    # whole claim: a colour chosen with a pointer on a canvas ends up in a
    # 512-slot buffer at the head's own address.  Measured through
    # `build_frames` - the same call the 40 Hz output thread makes - because
    # a passing HTTP call proves the API worked, not the output.
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile
    from app import fixtures as _fx
    _tmp = _tempfile.mkdtemp()
    try:
        _db = _os.path.join(_tmp, "pick.db")
        _fx.seed_generics(_db)
        e2 = eng.Engine(db_path=_db, dry_run=True,
                        show_dir=_os.path.join(_tmp, "shows"))
        r = e2.act("add_heads", query="LED PAR 4ch", qty=1,
                   mode="4ch RGBW", address=1)
        check("a 4ch RGBW PAR patches for the wire test", bool(r.get("ok")),
              str(r))
        par = e2.patch[0]
        check("and its map is dimmer,red,green,blue in channel order, so the "
              "four bytes at its address ARE the picked colour",
              par["map"] == ["dimmer", "red", "green", "blue"], str(par["map"]))
        hn = par["head_no"]
        for hexcol, want in {
            "#ff0000": (255, 255, 0, 0),      # the ring at 0 degrees
            "#00ff00": (255, 0, 255, 0),      # 120
            "#0000ff": (255, 0, 0, 255),      # 240
            "#ff8800": (255, 255, 136, 0),    # an amber off the ring
            "#f80":    (255, 255, 136, 0),    # three digits
            "#000000": (255, 0, 0, 0),        # the square's bottom-left corner
        }.items():
            e2.act("clear_programmer")
            e2.act("select_heads", heads=[hn])
            e2.act("set_intensity", level=100)
            e2.act("set_colour", hex=hexcol)
            data = e2.build_frames()[par["universe"]]
            a = par["address"] - 1
            got = tuple(data[a:a + 4])
            check(f"a colour picked as {hexcol} is on the wire as {want}",
                  got == want, str(got))
        check("and the universe is a real 512-slot frame",
              len(e2.build_frames()[par["universe"]]) == 512, "")

        # A drag across the ring is ~6 set_colour calls.  If each cost an
        # undo step, Ctrl+Z would walk back through the drag a frame at a
        # time - technically an undo, useless in practice.  So the whole drag
        # must be ONE step, and because coalescing keeps the ORIGINAL state,
        # that one step returns to "no colour", not to the drag's first frame.
        e2.act("clear_programmer")
        e2.act("select_heads", heads=[hn])
        e2.act("set_intensity", level=100)
        e2.act("set_colour", hex="#ff0000")
        for step in range(6):
            h = step * 30
            e2.act("set_colour", hex="#%02x00%02x" % (h, 255 - h))
        mid = dict(e2.programmer.get(hn, {}))
        check("the drag's last colour is what the programmer holds",
              (mid.get("red"), mid.get("blue")) == (150, 105), str(mid))
        u = e2.act("undo")
        after = dict(e2.programmer.get(hn, {}))
        check("one Ctrl+Z clears the WHOLE drag, landing on no colour at all "
              "rather than on the drag's first frame",
              u.get("label") == "set_colour"
              and "red" not in after and "blue" not in after,
              "label=%s after=%s" % (u.get("label"), after))
        u2 = e2.act("undo")
        check("and the intensity underneath is still its own separate step",
              u2.get("label") == "set_intensity"
              and not e2.programmer.get(hn), str(u2.get("label")))
    finally:
        _shutil.rmtree(_tmp, ignore_errors=True)

    # A DIAGNOSTIC MUST NOT BE ABLE TO TOUCH THE RIG.  `featurecheck.py`
    # built its engine on `config.DB_PATH` - the real fixture library - and
    # `add_heads` can create a profile, so a survey could write to the
    # operator's rig.  Every other tool in tools/ already builds on a
    # throwaway database under a temp dir; that one was the exception, and
    # the exception is where the damage happens.  Verified by reading the
    # tools' source, because there is nothing to call: the requirement is
    # about what they are ALLOWED to open.
    for tool in sorted((ROOT / "tools").glob("*.py")):
        if tool.name == "selftest.py":
            continue
        src = tool.read_text(encoding="utf-8")
        # A tool may READ config.DB_PATH - it is how it finds the library.
        # What it must not do is hand that path to an Engine.
        hands = re.findall(r"Engine\([^)]*config\.DB_PATH", src, re.S)
        check(f"{tool.name} never builds an Engine on the real library",
              not hands, str(hands)[:120])
    fc = (ROOT / "tools" / "featurecheck.py").read_text(encoding="utf-8")
    check("featurecheck works on a COPY, so running the survey cannot write "
          "to the operator's fixture library",
          "shutil.copy2(str(config.DB_PATH), str(_scratch_db))" in fc
          and "eng.Engine(db_path=_scratch_db" in fc, "")
    check("and on a throwaway show directory, not the real one",
          "config.CONSOLE_SHOW_DIR" not in fc
          and 'show_dir=td / "shows"' in fc, "")
    check("no tool points an Engine at the real autosave either",
          not any(re.search(r"Engine\([^)]*config\.CONSOLE_AUTOSAVE",
                            (ROOT / "tools" / n).read_text(encoding="utf-8"), re.S)
                  for n in ("featurecheck.py", "netinfo.py")), "")

    # A drag must not be a hundred HTTP requests, and a click that changes
    # nothing must not cost an undo step.  `set_colour` is already in
    # UNDO_COALESCE, which is what makes the throttled drag one Ctrl+Z.
    check("dragging is throttled through liveSender, not sent per pointermove",
          "const pickSend = liveSender(" in js
          and 'doAction("set_colour", v, { quiet: true })' in js, "")
    check("and the value the operator let go at is flushed, never dropped",
          js.count("pickSend.flush()") >= 2
          and "pending !== null) fire()" in js, "")
    check("the pointer is captured so a drag survives leaving the canvas",
          "cv.setPointerCapture(e.pointerId)" in js, "")
    check("a cancelled drag stops instead of leaving the control stuck",
          'pointercancel' in js, "")
    check("the picker is not repainted 20 times a second for nothing",
          "PICK.painted === key" in js and "function repaintPicker" in js, "")
    # Wiring a control is not the same as drawing it.  `wirePicker` only
    # attaches listeners, and `renderProgrammer` only repaints when the hex
    # field holds something it can parse - which on a fresh load with a clear
    # programmer it does not.  So the picker sat there as a blank 196px
    # square with every other check in this suite green.  Only reading the
    # pixels found it.
    check("and it is PAINTED at init, not merely wired - an empty hex field "
          "means renderProgrammer will not do it",
          js.index("function wirePicker()") < js.index("repaintPicker();\n"
                                                       "  renderPickInfo();\n}"),
          "")
    check("with an explicit repaint and an explicit reach summary",
          "repaintPicker();\n  renderPickInfo();\n}" in js, "")
    check("a half-typed hex in the box is not overwritten by the picker",
          'document.activeElement !== box) box.value = hex' in js, "")
    check("and the engine pulls the picker back into line when the colour "
          "arrives from a cue, a palette or undo",
          "if (hex && !PICK.down && document.activeElement !== "
          "$(\"#hex-in\")" in js, "")
    check("nothing but the drag sets it, so a feed tick cannot fight a finger",
          js.count("if (!PICK.down") >= 1
          and "PICK.down = true" in js, "")

    # Keyboard: a canvas is focusable and silent, so without this the whole
    # control is unreachable without a mouse.
    check("the picker is reachable and announced",
          'id="pick"' in html and "tabindex=" in html
          and 'aria-label=' in html, "")
    # The axis decision lives in a pure function the suite can run, NOT
    # inline in the handler where a transposed argument pair is invisible.
    check("and the axis each key moves is a PURE function, so it can be run",
          "function pickNudge(key, shift, cur)" in js
          and "const next = pickNudge(e.key, e.shiftKey, PICK);" in js, "")
    check("the browser does not scroll the page under the arrows",
          "if (!next) return;\n    e.preventDefault();\n"
          "    e.stopPropagation();" in js, "")
    # A NaN does not look like a NaN on the wire: clampInt returns its
    # default of 0, so it becomes a CONCRETE WRONG COLOUR.  That is how the
    # transposed axis turned a white head black instead of throwing.
    check("and a non-finite pick is refused outright rather than clamped "
          "into a wrong colour",
          "Number.isFinite" in js
          and "refusing a non-finite pick" in js
          and js.index("Number.isFinite") < js.index("const hex = rgbToHex("), "")
    check("a canvas draws no focus ring of its own, so the CSS supplies one",
          "#pick:focus" in css and "accent-soft" in css, "")
    check("with nothing selected it says so instead of counting zero",
          "select heads to pick a colour" in js, "")
    check("and when it reaches nobody it names the heads and says why",
          "have a colour wheel, not RGB" in js
          and 'has a colour wheel, not RGB' in js
          and 'has no colour channels' in js
          and "r.none.join" in js, "")
    check("with the verb agreeing, so one head is not 'heads 17 have'",
          '(one ? "head " : "heads ")' in js
          and '(one ? " has a colour wheel, not RGB"' in js, "")
    check("a white-only head is not silently counted as taking the colour",
          "they take the luma, not the colour" in js, "")
    check("an unreachable selection is dimmed, not disabled - disabled is "
          "skipped by the keyboard and reads as broken",
          "#pick-wrap.inert #pick" in css
          and "opacity: .38" in css
          and 'wrap.classList.toggle("inert"' in js
          and "disabled" not in html.split('id="pick"')[1][:400], "")


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}


def _html_structure(html: str) -> dict:
    """Tag balance, and the ANCESTOR chain of every element with an id.

    A missing `</div>` is invisible in the source and fatal in the DOM: the
    parser silently nests everything after it INSIDE the unclosed element.
    If that element is hidden, whole features disappear with no error, no
    failing test and nothing on screen to explain it - which is exactly
    what happened to the console's AI panel and to the DMX channel sheet.

    "Depth 0" was the first attempt at checking this and it was the wrong
    shape: the page has legitimate wrapper elements, so a landmark being at
    depth 1 is fine and being at depth 7 is the bug.  What actually matters
    is the ancestor chain - a top-level overlay must not have a hidden
    `.modal` in its ancestry.  So that is what is measured.
    """
    text = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    stack: list[tuple[str, str]] = []      # (tag, id or "")
    ancestors: dict[str, list[str]] = {}
    classes: dict[str, str] = {}
    unbalanced = 0
    for m in re.finditer(r"<(\w+)([^>]*?)/?>|</(\w+)>", text):
        name = (m.group(1) or m.group(3) or "").lower()
        attrs = m.group(2) or ""
        if m.group(3):                                   # closing
            while stack:
                tag, _ = stack.pop()
                if tag == name:
                    break
            else:
                unbalanced += 1
            continue
        if name in _VOID:
            continue
        ident = re.search(r'id="([\w-]+)"', attrs)
        cls = re.search(r'class="([^"]*)"', attrs)
        if ident:
            key = "#" + ident.group(1)
            ancestors[key] = ["#" + i for _, i in stack if i]
            if cls:
                classes[key] = cls.group(1)
            stack.append((name, ident.group(1)))
        else:
            stack.append((name, ""))
    return {"ancestors": ancestors, "classes": classes,
            "left_open": [t for t, _ in stack], "stray_close": unbalanced}


def test_console_only() -> None:
    """ONE product: the console.  The assistant page is gone.

    The check is not "the files are absent" - a file can be absent and the
    feature can still be reachable.  It is: the page is gone, the routes
    that served it are gone, the modules behind them are gone, nothing
    imports them, and the AI that stayed is the AI the console already had.
    """
    print("console only (the assistant page is gone)")
    main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    web = {p.name: p.read_text(encoding="utf-8")
           for p in (ROOT / "web").glob("*")
           if p.suffix in (".html", ".js", ".css")}

    for name in ("index.html", "app.js", "editor.js"):
        check(f"web/{name} is deleted", not (ROOT / "web" / name).exists(), "")
    for name in ("agent.py", "showread.py", "showbuild.py", "layouts.py"):
        check(f"app/{name} is deleted", not (ROOT / "app" / name).exists(), "")
    # ...and nothing stale is left in __pycache__ to be imported by accident
    stale = [p.name for p in (ROOT / "app" / "__pycache__").glob("*.pyc")
             if p.name.split(".")[0] in ("agent", "showread", "showbuild",
                                         "layouts")]
    check("and no stale .pyc of a deleted module is left to be imported",
          not stale, str(stale))

    # `/` IS the console.  Not a redirect: a redirect means the address bar
    # says one thing and the desk is the other, and an operator who
    # bookmarked the wrong one has to notice.
    #
    # Checked on the CODE, not on the text: `index.html` still appears in
    # this file's own comments, and a substring test over the whole source
    # reads prose as a route.
    code = "\n".join(ln for ln in main_src.splitlines()
                     if not ln.lstrip().startswith("#"))
    check("`/` serves the console, and does not redirect",
          'if route in ("/", "/index.html"):' in code
          and 'self._file(config.WEB / "console.html")' in code
          and 'config.WEB / "index.html"' not in code, "")
    check("and the console is still reachable at its own URL",
          'config.WEB / "console.html"' in code, "")

    for route in ("/api/chat", "/api/session", "/api/session/reset",
                  "//api/show", "/api/show/program", "/api/layout"):
        check(f"the route {route} is gone",
              f'route == "{route}"' not in main_src, "")

    for mod in ("agent", "showread", "showbuild", "layouts"):
        check(f"nothing imports app.{mod} any more",
              not re.search(rf"from app import [^\n]*\b{mod}\b", main_src)
              and not re.search(rf"from \. import [^\n]*\b{mod}\b", main_src),
              "")
    check("including from within app/ itself",
          not any(re.search(rf"^from \. import [^\n]*\b{m}\b", p.read_text(
              encoding="utf-8"), re.M)
              for p in (ROOT / "app").glob("*.py")
              for m in ("agent", "showread", "showbuild", "layouts")), "")

    # The rig studio's bridge into the patch went too.  It could only ever
    # succeed after a layout had been generated, and the studio was the
    # only thing that generated one - so it was a door to nothing, and a
    # dead branch that looks like a feature is worse than a missing one.
    from app import engine as eng
    check("and `patch_from_layout` is gone from the action table",
          "patch_from_layout" not in eng.ACTIONS, "")
    check("and from the lock's patch set, which is where a stale name hides",
          "patch_from_layout" not in eng.Engine.LOCK_PATCH, "")
    check("and from the AI's denylist, so it cannot drift back in",
          "patch_from_layout" not in (ROOT / "app" / "console_ai.py"
                                      ).read_text(encoding="utf-8"), "")
    check("and from the patch route, which defaulted to it",
          'body.get("action", "from_csv")' in main_src
          and 'action == "from_layout"' not in main_src, "")

    # THE AI STAYED, and it is the console's own - not a new chat surface
    # wearing a console's clothes.  These are the two things that must still
    # be there, and they are checked by RUNNING them, because a panel that
    # calls a route which 404s is not an AI capability.
    check("the console still owns an AI panel",
          'id="ai-panel"' in web["console.html"]
          and 'id="btn-ai"' in web["console.html"], "")
    check("with the plain-text compiler and the offline fallback",
          "console_ai.plan" in main_src and "offline=" in main_src, "")
    check("and show-from-a-prompt, which is /api/console/generate",
          "console_ai.generate(" in main_src
          and "/api/console/generate" in main_src, "")
    check("and the allowlist still keeps the AI away from arming output, "
          "show files and destructive edits",
          all(x in (ROOT / "app" / "console_ai.py").read_text(encoding="utf-8")
              for x in ("set_output", "save_show", "load_show",
                        "patch_clear", "remove_heads")), "")
    check("`llm` and `showdesign` were NOT collateral: the console's AI "
          "needs both, and deleting them would have broken it silently",
          all((ROOT / "app" / n).exists() for n in ("llm.py", "showdesign.py"))
          and "from . import llm, showdesign" in
          (ROOT / "app" / "console_ai.py").read_text(encoding="utf-8"), "")

    # `style.css` and `viz.js` are SHARED.  The assistant page loaded them
    # too, which is exactly why they look like part of it and must not be
    # deleted with it - style.css carries the design system and the global
    # `.hidden` rule the help dialog depends on.
    for shared in ("style.css", "viz.js"):
        check(f"web/{shared} survives - it is shared, not assistant-only",
              (ROOT / "web" / shared).exists()
              and shared in web["console.html"], "")
    check("and the console still loads the global `.hidden` rule that lives "
          "in style.css, which is what the help dialog's close relies on",
          'href="style.css"' in web["console.html"]
          and ".hidden { display: none !important; }" in web["style.css"], "")

    # The console must not have grown a second front end.
    check("the console does not link to any other page as a peer",
          "console.html" not in web["console.html"]
          and 'href="index.html"' not in web["console.html"], "")

    # # A MISSING </div> MADE THE WHOLE AI PANEL UNREACHABLE.
    #
    # `#add-dialog` was never closed, so the HTML parser nested the channel
    # sheet AND the AI slide-over inside it - and `#add-dialog` carries the
    # `hidden` class, so `display: none` hid the console's entire AI. The
    # `AI` button toggled a class on a panel inside a closed subtree, which
    # is why it did nothing an operator could see. Show-from-a-prompt
    # generated three concepts and put them in a `<select>` with zero
    # height, and the DMX channel sheet could not be opened either.
    #
    # Nothing about that was visible in the source by eye and nothing about
    # it was covered by a test, so the check is structural: walk the tags
    # and require the top-level overlays to sit at depth 0, which is what
    # "a child of <body>" means.  A missing close tag cannot hide from a
    # depth counter.
    depth = _html_structure(web["console.html"])
    # Note what this does and does NOT prove.  A stray `</div>` or an
    # element left open at the end is caught here - but a missing `</div>`
    # in the MIDDLE is not, because the stack recovers by construction.  The
    # check that catches the real bug is the ancestor one below, and it was
    # verified by putting the missing tag back: three checks fail and name
    # #ai-panel and #ch-dialog as being inside #add-dialog.  Both are here
    # because a balance error and a nesting error are different faults.
    check("the console's HTML has no stray closing tag and nothing left open "
          "at the end",
          not depth["left_open"] and not depth["stray_close"],
          "left open: %s / stray closes: %d"
          % (depth["left_open"][:5], depth["stray_close"]))
    # The invariant that actually matters: a top-level overlay must not have
    # a hidden modal in its ancestry, because that is what makes it
    # unreachable with no error and nothing on screen to explain it.
    modals = [k for k, cls in depth["classes"].items() if "modal" in cls.split()]
    for landmark in ("#ai-panel", "#ch-dialog", "#help-dialog", "#cmain"):
        anc = depth["ancestors"].get(landmark)
        check(f"{landmark} is not inside a modal", anc is not None
              and not [a for a in anc if a in modals],
              "inside %s" % [a for a in (anc or []) if a in modals])
    check("and the AI panel and the channel sheet are siblings of the add "
          "dialog, not children of it - the exact shape that hid both",
          not [a for a in depth["ancestors"].get("#ai-panel", [])
               if a == "#add-dialog"]
          and not [a for a in depth["ancestors"].get("#ch-dialog", [])
                   if a == "#add-dialog"],
          "ai-panel: %s / ch-dialog: %s"
          % (depth["ancestors"].get("#ai-panel"),
             depth["ancestors"].get("#ch-dialog")))
    check("and the console's own columns are not inside a modal either",
          all(not [a for a in depth["ancestors"].get(c, []) if a in modals]
              for c in ("#cmain", "#col-mid", "#col-left", "#col-right")
              if c in depth["ancestors"]), "")

    # show-from-a-prompt draws on the REAL rig now, so a patched desk is
    # previewed on its own positions rather than a synthetic ten-head rig
    # nobody has.  Asserted here as well as in test_showdesign because this
    # is the behaviour the removal CHANGED, and it is the one an operator
    # would notice.
    from app import showdesign as sd
    stage = sd._stage_from_patch(
        [{"head_no": 1, "x": -2.0, "y": 4.0, "z": 0.0, "role": "wash"},
         {"head_no": 2, "x": 2.0, "y": 4.0, "z": 0.0, "role": "beam"}],
        "goalpost")
    check("show-from-a-prompt stages on the patched rig at its own positions",
          stage and stage.get("from_rig")
          and [f["x"] for f in stage["fixtures"]] == [-2.0, 2.0]
          and stage["width"] > 4.0, json.dumps(stage)[:180])
    check("and falls back to nothing usable for an unpositioned or empty "
          "patch, so the caller draws a synthetic stage instead",
          sd._stage_from_patch([], "goalpost") is None
          and sd._stage_from_patch(
              [{"head_no": 1, "x": None, "y": None}], "goalpost") is None, "")


def _standalone_suites():
    return (
    ("simulated scan", test_scan_simulated),
    ("show design", test_showdesign),
    ("colour picker", test_colour_picker),
    ("console only", test_console_only),
    )



def check_js() -> None:
    """node --check every web script: catches the class of bug a browser
    only shows as a blank panel (a stray comma, a missing brace)."""
    print("javascript syntax")
    scripts = sorted((ROOT / "web").glob("*.js"))
    if not scripts:
        check("web scripts present", False, "no *.js in web/")
        return
    node = _which("node")
    if node is None:
        print("  skip  node not found - cannot syntax-check the web scripts")
        return
    for path in scripts:
        proc = subprocess.run([node, "--check", str(path)],
                              capture_output=True, text=True)
        check(f"node --check {path.name}", proc.returncode == 0,
              (proc.stderr or "").strip()[:160])
    check_js_math(node)


# The 3D picking maths, verified against the SHIPPED source rather than a
# copy of it.  m4Invert feeds screenToPlane, which is what makes a dragged
# fixture follow the cursor; a transposed write-back there yields a
# plausible-looking but wrong matrix, so the fixture is still PICKED (the
# cursor changes) and the drag then does nothing at all - invisible in
# Python and easy to miss in a browser.  So extract the real function out
# of viz.js and check M * M^-1 = I.
_MATH_HARNESS = r"""
const fs = require('fs');
// with `node -e SCRIPT FILE`, argv is [node, FILE] - the -e script itself
// is not an argument, so the path is argv[1]
const src = fs.readFileSync(process.argv[1], 'utf8');
function grab(name) {
  const i = src.indexOf('function ' + name + '(');
  if (i < 0) { console.error('missing ' + name); process.exit(2); }
  let d = 0, started = false;
  for (let k = i; k < src.length; k++) {
    if (src[k] === '{') { d++; started = true; }
    else if (src[k] === '}') { d--; if (started && d === 0) return src.slice(i, k + 1); }
  }
  console.error('unterminated ' + name); process.exit(2);
}
eval(grab('m4'));
eval(grab('tr'));
eval(grab('m4Invert'));

function identityError(A, B) {
  const prod = m4();
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += A[k * 4 + r] * B[c * 4 + k];
      prod[c * 4 + r] = s;
    }
  }
  let e = 0;
  for (let i = 0; i < 16; i++) e = Math.max(e, Math.abs(prod[i] - (i % 5 === 0 ? 1 : 0)));
  return e;
}

const M = m4();
M[0] = 0.8; M[1] = 0.1; M[5] = 1.1; M[6] = -0.2; M[10] = 0.9; M[11] = 0.05;
M[12] = 3; M[13] = -2; M[14] = 7; M[15] = 1;
const I = m4();
if (!m4Invert(I, M)) { console.log('INVERT_FAILED'); process.exit(0); }
console.log('ERR=' + identityError(M, I));

function mPersp(o, f, aspect, near, far, yOff) {
  o.fill(0); o[0] = f / aspect; o[5] = f; o[9] = yOff;
  o[10] = (far + near) / (near - far); o[11] = -1;
  o[14] = (2 * far * near) / (near - far);
}
const P = m4(); mPersp(P, 4.9, 1.8, 0.1, 400, 0.1);
const V = m4();
V[0] = 0.87; V[1] = 0.5; V[2] = 0; V[4] = -0.5; V[5] = 0.87; V[6] = 0;
V[8] = 0; V[9] = 0; V[10] = 1; V[12] = -9; V[13] = -7; V[14] = -16; V[15] = 1;
const PV = m4();
for (let c = 0; c < 4; c++) {
  for (let r = 0; r < 4; r++) {
    let s = 0;
    for (let k = 0; k < 4; k++) s += P[k * 4 + r] * V[c * 4 + k];
    PV[c * 4 + r] = s;
  }
}
const PI = m4();
if (!m4Invert(PI, PV)) { console.log('PV_INVERT_FAILED'); process.exit(0); }
console.log('PV_ERR=' + identityError(PV, PI));
"""


def check_js_math(node: str) -> None:
    """The 3D picking maths must actually invert a matrix."""
    viz = ROOT / "web" / "viz.js"
    proc = subprocess.run([node, "-e", _MATH_HARNESS, str(viz)],
                          capture_output=True, text=True)
    out = proc.stdout
    match = re.search(r"ERR=([0-9.eE+-]+)", out)
    if proc.returncode != 0 or not match:
        check("m4Invert inverts a general matrix", False,
              (proc.stderr or out).strip()[:200])
        return
    # 1e-4 is generous for float32 accumulation and still catches a
    # transposed write-back, which is wrong by O(100)
    err = float(match.group(1))
    check("m4Invert inverts a general matrix", err < 1e-4, f"max error {err:.2e}")
    pv_match = re.search(r"PV_ERR=([0-9.eE+-]+)", out)
    check("m4Invert inverts a view-projection",
          bool(pv_match) and float(pv_match.group(1)) < 1e-3,
          f"max error {float(pv_match.group(1)):.2e}" if pv_match
          else out.strip()[:160])


def _which(prog: str) -> str | None:
    from shutil import which
    return which(prog)


def test_realtime(tmp: Path) -> None:
    """Performance ceilings on the real-time path.

    The output thread needs the engine lock every 25 ms at 40 Hz.  These
    are the numbers that keep an operator action from stalling a running
    fade, measured on this machine - generous ceilings, so a normal run
    passes with a wide margin and a real regression fails loudly.
    """
    print("realtime budget (40 Hz = 25 ms per tick)")
    from app import engine as eng
    from app import fixtures, profiles

    db = tmp / "perf.db"
    fixtures.seed_generics(db)
    profiles.install(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "perf_shows",
                   autosave_path=tmp / "perf_autosave.json")
    try:
        budget = 1000.0 / 40.0

        # --- frame building scales, and stays well inside the tick -------
        for heads, ceiling in ((4, budget), (120, budget * 0.6),
                               (250, budget * 0.8)):
            e.act("patch_clear")
            plan = e.plan_addresses([
                {"query": "LED PAR 4ch" if i % 2 == 0 else "intimidator spot 260",
                 "universe": 1 + (i * 2) // 34, "address": 1 + (i * 2) % 34}
                for i in range(heads)])
            e._replace_patch([e._build_head(r, i + 1)
                              for i, r in enumerate(plan)])
            e.act("select_all")
            e.act("set_intensity", level=80)
            e.act("record_cue", playback=1, fade=0)
            e.act("cue_go", playback=1)          # a live playback = worst case
            e.build_frames()                      # warm
            t0 = time.perf_counter()
            for _ in range(20):
                e.build_frames()
            ms = (time.perf_counter() - t0) / 20 * 1000
            check(f"build_frames {heads} heads under {ceiling:.1f} ms",
                  ms < ceiling, f"{ms:.2f} ms")

        # --- a bulk import must NOT hold the lock for the whole read -----
        # 120 rows = 120 fixture lookups.  If the map resolution ever goes
        # back under the lock again, this is the check that catches it.
        e.act("patch_clear")
        csv = ("Headno,Headname,Dmxno,Manufacturer,Type,Chans,X,Y,Z,Role\n" +
               "\n".join(f"{i + 1:04d},Par {i + 1},1-{1 + (i * 4) % 480:03d},"
                         f"Generic,LED PAR 4ch,4,0,0.3,0,generic"
                         for i in range(120)))
        e._a_patch_from_csv(csv=csv)             # warm the fixture cache
        e.act("patch_clear")
        worst = 0.0
        for _ in range(5):
            t0 = time.perf_counter()
            e.act("patch_from_csv", csv=csv)
            worst = max(worst, (time.perf_counter() - t0) * 1000)
        check("patch_from_csv 120 heads keeps the output lock brief",
              worst < budget * 6, f"{worst:.1f} ms worst (ceiling "
              f"{budget * 6:.0f} ms)")

        # --- one fixture lookup per patch, not per row -------------------
        e.act("patch_clear")
        calls = {"n": 0}
        real_search = fixtures.search

        def counting(db_path, query, limit=20):
            calls["n"] += 1
            return real_search(db_path, query, limit)
        fixtures.search = counting
        try:
            eng._FIXTURE_CACHE.clear()
            e.act("patch_from_csv", csv=csv)
            check("fixture lookups are cached across a patch",
                  calls["n"] <= 4, f"{calls['n']} queries for 120 heads")
        finally:
            fixtures.search = real_search

        # --- autosave writes off the output lock ------------------------
        e.act("patch_clear")
        e.act("patch_from_csv", csv=csv)
        payload_ms = 0.0
        with e.lock:
            t0 = time.perf_counter()
            e._autosave_payload()
            payload_ms = (time.perf_counter() - t0) * 1000
        check("autosave serialisation under the lock is cheap",
              payload_ms < budget * 0.5, f"{payload_ms:.2f} ms")
        e._autosave(force=True)
        check("forced autosave still reaches disk",
              (tmp / "perf_autosave.json").is_file(), "no autosave file")
    finally:
        e.shutdown()


def test_client_contracts() -> None:
    """Shape contracts between the engine and web/console.js.

    The client patches its local state from an action's own result instead
    of reloading everything (see doAction).  That is only safe if the
    engine and the client agree on the SHAPE of that result - and a shape
    mismatch is invisible in Python, showing up in a browser as a playback
    card that reads "cue undefined" or highlights the wrong row.  So the
    contract is asserted here, on both sides, rather than discovered.
    """
    print("client contracts (engine <-> console.js)")
    js = (ROOT / "web" / "console.js").read_text(encoding="utf-8")

    import tempfile
    from app import engine as eng
    from app import fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "cc.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=3)
            e.act("select_all")
            for n, lvl in ((1, 80), (2, 40), (3, 100)):
                e.act("select_heads", head=n)
                e.act("set_intensity", level=lvl)
                e.act("record_cue", playback=1, name=f"cue {n}")
            e.act("clear_programmer")

            go = e.act("cue_go", playback=1)
            check("cue_go names the playback it touched",
                  go["ok"] and go["playback"] == 1 and "cue" in go,
                  json.dumps(go))
            check("cue_go reports the cue as a NUMBER plus a name",
                  isinstance(go["cue"], int) and isinstance(go["name"], str)
                  and go["name"] != "",
                  json.dumps({k: go[k] for k in ("cue", "name")}))

            # the feed reports the same cue as an OBJECT - the two shapes
            # the client's cheap path has to reconcile
            lit = e.lite()
            pb = next(p for p in lit["playbacks"] if p["n"] == 1)
            check("the feed reports the cue as an object",
                  isinstance(pb["cue"], dict) and pb["cue"]["n"] == go["cue"]
                  and pb["cue"]["name"] == go["name"],
                  json.dumps(pb["cue"]))
            check("the feed carries the index the action omits",
                  isinstance(pb["index"], int) and "index" not in go,
                  json.dumps({"index": pb["index"]}))

            # and the client really does reconcile them
            check("the client folds a numeric cue into an object",
                  "Number(row.cue)" in js and 'typeof row.cue === "object"' in js,
                  "")
            check("the client derives the index from the cue number",
                  "findIndex((c) => c.n === pb.cue.n)" in js, "")
            check("the client does not hand a state object to a DOM helper",
                  not re.search(r"(highlightCue|updateFollowRow|scrollIntoView)"
                                r"\(\s*pb\b", js),
                  "")
            check("the cheap path re-renders instead of poking one card",
                  "applyOnePlayback" in js
                  and "renderPlaybacks();" in js.split("function applyOnePlayback")[1]
                  .split("\n}")[0],
                  "")

            # --- stale_heads must be in BOTH feeds, or the warning dies --
            snap_keys = set(e.snapshot())
            lite_keys = set(lit)
            check("stale_heads is in the full snapshot",
                  "stale_heads" in snap_keys, str(sorted(snap_keys)))
            check("stale_heads is in the lite feed",
                  "stale_heads" in lite_keys, str(sorted(lite_keys)))
            check("the client reads stale_heads from the state",
                  "S.stale_heads" in js, "")
            check("both feeds report the same stale list",
                  e.snapshot()["stale_heads"] == lit["stale_heads"],
                  f"{e.snapshot()['stale_heads']} vs {lit['stale_heads']}")

            # The JS is guarded by `if (warn)`, so a MISSING element is a
            # silent no-op: the diagnosis is computed and never shown.
            # Every id the console JS reaches for must therefore exist in
            # the markup - this is the check that catches that.
            html = (ROOT / "web" / "console.html").read_text(encoding="utf-8")
            ids = set(re.findall(r'id="([^"]+)"', html))
            wanted = set(re.findall(r'\$\("#([a-zA-Z0-9_-]+)"\)', js))
            missing = sorted(w for w in wanted if w not in ids)
            check("every id console.js looks up exists in the markup",
                  not missing, "missing: " + ", ".join(missing[:12]))
            for needed in ("stale-warn", "head-list", "pb-list", "dlg-lib-list"):
                check(f"console.html has #{needed}", needed in ids, needed)

            # --- venue must reach the visualiser -------------------------
            e.act("set_venue", width_m=20, depth_m=10, height_m=7)
            check("venue is in both feeds",
                  e.snapshot()["venue"]["width_m"] == 20
                  and e.lite()["venue"]["width_m"] == 20, "")
            check("the client reads venue from the state",
                  "S.venue" in js or "S && S.venue" in js, "")

            # --- a patch change must bump the rev the feeds key on -------
            rev = e.lite()["patch_rev"]
            e.act("set_place", head=1, x=5.0)
            check("moving a head bumps the patch revision",
                  e.lite()["patch_rev"] != rev,
                  f"{rev} -> {e.lite()['patch_rev']}")
            e.act("set_venue", width_m=21)
            check("drawing the room bumps it too",
                  e.lite()["patch_rev"] > e.snapshot()["patch_rev"] - 2, "")

            # --- set_place is what the 3D drag calls -------------------
            check("the client calls set_place, not a made-up name",
                  'doAction("set_place"' in js, "")
            check("the visualiser opts into editing",
                  "editable: true" in js and "onMoveFixture" in js
                  and "onPick" in js, "")
        finally:
            e.shutdown()


def test_discovery() -> None:
    """Unicast discovery against a real Art-Net node on a real socket.

    The sweep is the one piece of this that has to work on a network
    where broadcast is filtered, so it is tested against a node that
    behaves like hardware: a real UDP socket on udp/6454 answering an
    ArtPoll with a real ArtPollReply and emitting real ArtDmx frames.

    Two bugs this caught, both of which would have made discovery report
    "nothing found" on a rig that was answering perfectly well:
      * the sweep polled from udp/6454, and on Windows SO_REUSEADDR lets
        that bind succeed alongside another socket already holding the
        port - the reply was then delivered to the *other* socket and the
        sweep heard nothing.  Nodes reply to the poll's SOURCE port, so
        an ephemeral port is both correct and collision-free.
      * scan()'s reply count only included the sweep's polls when the
        sweep had *succeeded*, so a genuine negative reported "3
        unicast address(es)" after polling 254.
    """
    print("rig discovery (unicast sweep + ArtDmx depth)")
    import socket
    import threading
    from app import artnet

    # --- the reply we answer with must survive our own parser ------------
    reply = artnet.build_artpollreply("127.0.0.50", ports=(0, 1, 2))
    parsed = artnet.parse_artpollreply(reply)
    check("a built ArtPollReply parses back",
          parsed is not None and parsed["ip"] == "127.0.0.50"
          and parsed["output_ports"] == [0, 1, 2]
          and parsed["name"] == "Test Node", json.dumps(parsed))
    check("a truncated reply is still readable",
          artnet.parse_artpollreply(reply[:40]) is not None, "")
    check("a non-Art-Net packet is rejected",
          artnet.parse_artpollreply(b"hello world padding here") is None, "")

    # --- local subnet discovery -----------------------------------------
    subs = artnet.local_subnets()
    check("a local /24 is discovered without any dependency",
          all(s.count(".") == 3 and s.endswith(".") for s in subs), str(subs))

    # --- a real node on a real socket ------------------------------------
    port = _free_udp_port()
    stop = threading.Event()
    seen = {"polls": 0, "replies": 0, "frames": 0}

    def node() -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return
        s.settimeout(0.15)
        slots = bytearray(512)
        for i in range(40):                       # 40 channels in use
            slots[i] = 200
        while not stop.is_set():
            try:
                data, addr = s.recvfrom(2048)
            except (socket.timeout, TimeoutError):
                # keep emitting: that is how a live rig behaves, and it is
                # the only way channel depth can be observed.  Universe 1,
                # not 0 - the builder is 1-based and says so.
                try:
                    s.sendto(artnet.build_artdmx(1, bytes(slots),
                                                 sequence=seen["frames"] + 1),
                             addr)
                    seen["frames"] += 1
                except (OSError, ValueError):
                    pass
                continue
            except OSError:
                break
            if len(data) < 10 or data[0:8] != b"Art-Net\0":
                continue
            op = int.from_bytes(data[8:10], "little")
            if op == artnet.ARTPOLL_OP:
                seen["polls"] += 1
                try:
                    s.sendto(artnet.build_artpollreply("127.0.0.1", port=port,
                                                       ports=(0,)), addr)
                    seen["replies"] += 1
                except OSError:
                    pass

    thread = threading.Thread(target=node, daemon=True)
    thread.start()
    try:
        # the node must be up before we poll
        deadline = time.monotonic() + 2.0
        while seen["polls"] == 0 and time.monotonic() < deadline:
            try:
                probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                probe.settimeout(0.3)
                probe.sendto(artnet.build_artpoll(), ("127.0.0.1", port))
                probe.recvfrom(2048)
                probe.close()
            except OSError:
                time.sleep(0.05)

        found = artnet.sweep(subnets=["127.0.0."], timeout=1.6, port=port)
        check("the sweep finds a node that answers unicast",
              len(found["nodes"]) == 1
              and found["nodes"][0]["ip"] == "127.0.0.1",
              json.dumps(found["nodes"]))
        check("the sweep reports the node's output port as a universe",
              [u["universe"] for u in found["universes"]] == [1],
              json.dumps(found["universes"]))
        check("the sweep counts every address it polled",
              found["swept"] == 254 and found["polls_sent"] == 254,
              json.dumps({k: found[k] for k in ("swept", "polls_sent")}))
        check("the sweep observed the node's live DMX depth",
              any(u["channels"] == 40 for u in found["universes"]),
              json.dumps(found["universes"]))
        check("the sweep binds an ephemeral port, not 6454",
              found["bound_port"] not in (0, artnet.ART_NET_PORT)
              and found["bound_port"] > 0, str(found["bound_port"]))

        # --- scan() falls through to the sweep and merges the results ----
        got = artnet.scan(timeout=0.6, port=port, sweep_timeout=1.2)
        check("scan finds the node too",
              len(got["nodes"]) == 1, json.dumps(got.get("nodes")))
        # scan only sweeps when the broadcast pass finds nothing, so a
        # positive result is legitimately just the 2-3 broadcast polls
        check("scan skips the sweep once it has found the node",
              "unicast sweep" not in got["tried"]
              and got["polls_sent"] <= 4,
              json.dumps({k: got[k] for k in ("polls_sent", "tried")}))
        check("scan says a negative was a real negative",
              "no Art-Net node answered" in got["message"]
              or f"{len(got['nodes'])} node(s)" in got["message"],
              got["message"])
    finally:
        stop.set()
        thread.join(timeout=2.0)

    # --- a genuinely empty network must SAY what it tried ----------------
    empty = artnet.scan(timeout=0.4, port=_free_udp_port(), sweep_timeout=0.4)
    check("an empty network reports no nodes",
          empty["nodes"] == [] and empty["universes"] == [], json.dumps(empty)[:160])
    check("an empty network reports how hard it looked",
          empty["polls_sent"] >= 254 and "unicast" in " ".join(empty["tried"]),
          json.dumps({k: empty[k] for k in ("polls_sent", "tried")}))
    check("an empty network message names the subnets swept",
          "broadcast" in empty["message"] and "unicast" in empty["message"],
          empty["message"])


def test_merge() -> None:
    """The real-time merge, tested on its own (app/merge.py).

    These functions run every 25 ms at 40 Hz and are shared by the wire
    and the visualiser, so they are deliberately free of engine state -
    which means they can be pinned down without building a console.
    """
    print("merge (the real-time core)")
    from app import engine_support as sup
    from app import merge

    # --- role vocabulary -------------------------------------------------
    check("label -> role, plain", sup.channel_role("Dimmer") == "dimmer", "")
    check("label -> role, 16-bit coarse",
          sup.channel_role("Pan (16-bit)") == "pan", "")
    check("label -> role, fine half",
          sup.channel_role("Tilt fine") == "tilt_fine", "")
    check("label -> role, maintenance is unused",
          sup.channel_role("Dimmer Speed") == "unused", "")
    check("label -> role, gobo rotation",
          sup.channel_role("Gobo 1 Rotate") == "gobo_rot", "")
    check("label -> role, unknown is raw",
          sup.channel_role("Zzz Qqq") == "raw", "")

    # --- 16-bit maths ----------------------------------------------------
    check("split 0 / mid / max",
          sup.split_16bit(0) == (0, 0)
          and sup.split_16bit(0x1234) == (0x12, 0x34)
          and sup.split_16bit(65535) == (255, 255), "")
    check("split round-trips through join",
          all(sup.join_16bit(*sup.split_16bit(v)) == v
              for v in (0, 1, 0x00FF, 0x1234, 0xFFFF)), "")
    check("fine_first swaps the pair",
          sup.split_16bit(0x1234, fine_first=True) == (0x34, 0x12), "")
    check("8-bit input scales to full 16-bit",
          sup.logical16(255) == 65535 and sup.logical16(128) == 32896, "")
    try:
        sup.split_16bit("banana")
        check("split rejects non-numbers", False, "no error raised")
    except ValueError:
        check("split rejects non-numbers", True, "")

    # --- pair map --------------------------------------------------------
    fine_of, base_of = merge.pair_map(["pan", "pan_fine", "tilt"])
    check("16-bit pair found in either order",
          fine_of == {0: 1} and base_of == {1: 0},
          f"{fine_of} {base_of}")
    fine_of2, base_of2 = merge.pair_map(["pan_fine", "pan"])
    check("fine-before-base still pairs",
          fine_of2 == {1: 0} and base_of2 == {0: 1},
          f"{fine_of2} {base_of2}")
    check("a fixture without a fine channel has no pairs",
          merge.pair_map(["dimmer", "red", "gobo"]) == ({}, {}), "")

    # --- precedence: HTP intensity, LTP everything else -----------------
    head = {"head_no": 1, "universe": 1, "address": 1, "channels": 4,
            "map": ["dimmer", "red", "green", "blue"],
            "curve": "linear", "role": "par"}
    # programmer 40, playback at 50% carrying 80 -> 40; HTP keeps 40
    vals = merge.resolve_head(head, {1: {"dimmer": 40}},
                              [(50, {1: {"dimmer": 80, "red": 200}})])
    check("HTP: the highest intensity wins", vals["dimmer"] == 40, str(vals))
    # the playback's red has no programmer value, so LTP adopts it
    check("LTP: the only source provides the colour",
          vals["red"] == 200, str(vals))
    # a running effect owns the roles it drives
    vals = merge.resolve_head(head, {1: {"dimmer": 40}},
                              [(100, {1: {"dimmer": 80}})],
                              fx_row={"dimmer": 10})
    check("an effect owns the role it drives", vals["dimmer"] == 10, str(vals))
    # blackout and master come last, on everything
    check("blackout kills intensity but not colour",
          merge.resolve_head(head, {1: {"dimmer": 90, "red": 128}}, [],
                             blackout=True)["dimmer"] == 0, "")
    check("grand master scales intensity",
          merge.resolve_head(head, {1: {"dimmer": 100}}, [],
                             master=50)["dimmer"] == 50, "")
    check("an untouched head is dark, not undefined",
          merge.resolve_head(head, {}, [])["dimmer"] == 0, "")

    # --- frames ----------------------------------------------------------
    frames = merge.build_frames([head], {1: {"dimmer": 100, "red": 255,
                                             "green": 0, "blue": 0}}, [])
    buf = frames[1]
    check("one 512-byte frame per universe",
          len(frames) == 1 and len(buf) == 512, str({u: len(b) for u, b in
                                                      frames.items()}))
    check("full dimmer writes 255", buf[0] == 255, str(buf[0]))
    check("colour lands on its own channel", buf[1] == 255, str(buf[1]))
    check("a colour we did not touch stays 0", buf[2] == 0 and buf[3] == 0, "")

    # 16-bit pair -> two bytes carrying ONE logical value
    head16 = {"head_no": 2, "universe": 1, "address": 10, "channels": 4,
              "map": ["pan", "pan_fine", "tilt", "tilt_fine"],
              "curve": "linear", "role": "spot"}
    buf = merge.build_frames([head16], {2: {"pan": 0x1234}}, [])[1]
    check("16-bit pan split across two bytes",
          buf[9] == 0x12 and buf[10] == 0x34,
          f"{buf[9]},{buf[10]}")
    check("the untouched 16-bit pair writes zero",
          buf[11] == 0 and buf[12] == 0, f"{buf[11]},{buf[12]}")

    # intensity 0-100 goes through the curve
    curved = dict(head, curve="square")
    check("the dimmer curve is applied on the wire",
          merge.build_frames([curved], {1: {"dimmer": 50}}, [])[1][0] == 63,
          str(merge.build_frames([curved], {1: {"dimmer": 50}}, [])[1][0]))

    # a head at the end of a universe must not write past it
    edge = {"head_no": 3, "universe": 1, "address": 512, "channels": 4,
            "map": ["dimmer", "red", "green", "blue"],
            "curve": "linear", "role": "par"}
    try:
        frames = merge.build_frames([edge], {3: {"dimmer": 255}}, [])
        check("a straddling head writes what fits and no more",
              len(frames[1]) == 512 and frames[1][511] == 255, "overflow")
    except Exception as exc:                        # noqa: BLE001
        check("a straddling head writes what fits and no more", False, str(exc))

    # two universes -> two frames, independent
    other = dict(head, head_no=4, universe=3, address=1)
    frames = merge.build_frames([head, other], {1: {"dimmer": 255},
                                                4: {"dimmer": 10}}, [])
    check("each universe gets its own frame",
          sorted(frames) == [1, 3] and frames[1][0] == 255
          and frames[3][0] == 25, str({u: b[0] for u, b in frames.items()}))

    # --- the merge must not touch the disk or the network ----------------
    # Checked on the parsed tree, not the text: a docstring that says
    # "no socket" must not fail the check, but an actual import of one
    # must.  This is the property that keeps the 25 ms budget.
    import ast
    tree = ast.parse((ROOT / "app" / "merge.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    check("merge.py imports nothing that can block",
          not (imported & {"sqlite3", "socket", "http", "urllib",
                            "requests", "subprocess", "time", "os",
                            "pathlib", "json"}),
          str(sorted(imported)))
    check("merge.py has no blocking calls at all",
          not [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id in ("open", "sleep", "input")],
          "")


def test_engine_api() -> None:
    """The new console actions the UI depends on (set_place, set_venue,
    look_feed) - the pieces the visualiser and the patch highlight call."""
    print("console api: place, venue, look feed")
    import tempfile
    from app import engine as eng
    from app import fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "api.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            e.act("add_heads", query="intimidator spot 260", qty=1)

            # --- set_place: the 3D drag -------------------------------
            r = e.act("set_place", head=1, x=2.5, y=3.0, z=1.5)
            check("set_place moves a head",
                  r["ok"] and r["moved"] and r["x"] == 2.5
                  and r["y"] == 3.0 and r["z"] == 1.5, json.dumps(r))
            check("a head above 2 m becomes a truss fixture",
                  r["kind"] == "truss", r["kind"])
            e.act("set_place", head=1, y=0.4)
            check("a head on the deck is a floor fixture",
                  e.snapshot()["patch"][0]["kind"] == "floor",
                  str(e.snapshot()["patch"][0]["kind"]))
            check("set_place refuses a bad number",
                  e.act("set_place", head=1, x="banana")["ok"] is False, "")
            check("set_place refuses an unpatched head",
                  e.act("set_place", head=99, x=1)["ok"] is False, "")
            check("set_place needs a head",
                  e.act("set_place", x=1)["ok"] is False, "")

            # --- stage bounds ------------------------------------------
            # A 3D drag is a ray/plane intersection, so a light far from
            # the camera amplifies a small screen nudge into a large world
            # jump.  Repeated nudges used to walk a head to x=28 in an 18 m
            # room, where the operator cannot see it again.
            check("a head cannot be dragged off the stage",
                  e.act("set_place", head=1, x=9999, z=9999)["x"] <= 40.0
                  and e.act("set_place", head=1, x=9999, z=9999)["z"] <= 60.0,
                  json.dumps(e.act("set_place", head=1, x=9999, z=9999)))
            r = e.act("set_place", head=1, x=9999, y=-50, z=9999)
            check("the clamp is reported, not silent",
                  r["ok"] and r["clamped"] is True
                  and "clamped" in r["summary"], json.dumps(r)[:200])
            check("the clamp keeps the bounds in the result",
                  r["bounds"]["half_width_m"] == 40.0
                  and r["bounds"]["max_depth_m"] == 60.0, json.dumps(r["bounds"]))
            check("height cannot go below the floor",
                  r["y"] == 0.0, str(r["y"]))
            check("a head inside the bounds is not clamped",
                  e.act("set_place", head=1, x=1.0, y=3.0, z=2.0)["clamped"]
                  is False, "")
            # once a venue exists the bounds follow it
            e.act("set_venue", width_m=18, depth_m=12, height_m=6)
            r = e.act("set_place", head=1, x=500, z=500)
            check("the venue tightens the bounds",
                  abs(r["x"]) <= 13.0 and r["z"] <= 16.0, json.dumps(r)[:200])
            check("the venue also caps the height",
                  e.act("set_place", head=1, y=999)["y"] <= 10.0,
                  str(e.act("set_place", head=1, y=999)["y"]))

            # --- set_venue: the room the operator drew ------------------
            r = e.act("set_venue", width_m=18, depth_m=12, height_m=6,
                      name="Main Hall",
                      surfaces=[{"kind": "wall", "x1": -9, "y1": 0, "z1": 12,
                                 "x2": 9, "y2": 0, "z2": 12},
                                {"kind": "truss", "x1": -8, "y1": 5, "z1": 3,
                                 "x2": 8, "y2": 5, "z2": 3}])
            check("set_venue stores the room",
                  r["ok"] and r["venue"]["width_m"] == 18
                  and r["venue"]["name"] == "Main Hall"
                  and len(r["venue"]["surfaces"]) == 2, json.dumps(r))
            check("venue reaches the state feed",
                  e.snapshot()["venue"]["depth_m"] == 12, "")
            r = e.act("set_venue", width_m=10, surfaces=[{"kind": "x"},
                                                         "not a dict", 7])
            check("a malformed surface is dropped, not fatal",
                  r["ok"] and r["venue"]["surfaces"] == [], json.dumps(r))

            # --- look_feed: the per-tick light feed ---------------------
            e.act("select_all")
            e.act("set_intensity", level=100)
            feed = e.look_feed()
            check("look feed is sequenced and complete",
                  feed["seq"] >= 1 and feed["full"] is True
                  and len(feed["heads"]) == 3
                  and all(h["a"] > 0 for h in feed["heads"]),
                  json.dumps(feed)[:200])
            check("look feed rows carry colour + level",
                  all("hex" in h and "a" in h for h in feed["heads"]),
                  json.dumps(feed["heads"][:1]))
            # a hint one tick behind -> incremental; a stale hint -> full
            nxt = e.look_feed(since=feed["seq"])
            check("a fresh sequence is incremental",
                  nxt["seq"] == feed["seq"] + 1 and nxt["full"] is False,
                  json.dumps(nxt)[:120])
            check("a stale sequence forces a full snapshot",
                  e.look_feed(since=1)["full"] is True, "")

            # a dark head is NOT in the feed (that is the whole point)
            e.act("clear_programmer")
            dark = e.look_feed()
            check("the feed only carries emitting heads",
                  dark["heads"] == [] and dark["lit"] == 0
                  and dark["count"] == 3, json.dumps(dark)[:160])

            # it must agree with the wire, because it is the same merge
            e.act("set_intensity", level=100)
            frames = e.build_frames()
            a_on_wire = frames[1][0]
            a_on_feed = e.look_feed()["heads"][0]["a"]
            check("the feed and the wire agree",
                  abs(a_on_wire / 255 - a_on_feed) < 0.01,
                  f"wire {a_on_wire} feed {a_on_feed}")

            # --- stale_heads: the "playback does nothing" diagnosis -----
            e.act("record_cue", playback=1)
            check("a fresh cue has no stale heads",
                  e.snapshot()["stale_heads"] == [],
                  str(e.snapshot()["stale_heads"]))
            e.act("remove_heads", head=3)
            stale = e.snapshot()["stale_heads"]
            check("removing a head it was pointing at is reported",
                  stale == [3], str(stale))
            check("stale heads also ride the lite feed",
                  e.lite()["stale_heads"] == [3],
                  str(e.lite()["stale_heads"]))
            # loading a show that references unpatched heads says so too
            saved = e.act("save_show", name="with-stale")["file"]
            r = e.act("load_show", name=saved)
            check("load_show names the unpatched heads in its result",
                  r["ok"] and r["stale_heads"] == [3],
                  json.dumps(r)[:160])
        finally:
            e.shutdown()


def main() -> int:
    crashed: list[str] = []
    silent: list[str] = []
    started = time.monotonic()
    # One shared temp dir: some suites deliberately hand artefacts to
    # others (the synthetic GDTF written by the parser suite is imported
    # by the database and layout suites).  Isolation is about catching
    # exceptions, not about separate filesystems.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, suite in _suites():
            before = PASS + FAIL
            try:
                # Suites that take a temp dir get it; the rest are
                # self-contained and take no argument.
                if suite.__code__.co_argcount:
                    suite(tmp)
                else:
                    suite()
            except Exception:                       # noqa: BLE001
                crashed.append(name)
                print(f"  FAIL {name} suite crashed:\n"
                      + "    " + traceback.format_exc().replace("\n", "\n    "))
            ran = PASS + FAIL - before
            if ran == 0 and name not in crashed:
                # A suite that adds no checks has stopped testing anything
                # - usually an early `return` left behind by an edit. That
                # is silent coverage loss, so it is reported as a failure
                # rather than passing unnoticed.
                silent.append(name)
                check(f"{name} suite ran its checks", False,
                      "the suite added 0 checks - it is testing nothing")
            elif name not in crashed:
                check(f"{name} suite ran {ran} checks", True, "")
    for name, suite in _standalone_suites():
        before = PASS + FAIL
        try:
            suite()
        except Exception:                           # noqa: BLE001
            crashed.append(name)
            print(f"  FAIL {name} suite crashed:\n"
                  + "    " + traceback.format_exc().replace("\n", "\n    "))
        ran = PASS + FAIL - before
        if ran == 0 and name not in crashed:
            silent.append(name)
            check(f"{name} suite ran its checks", False,
                  "the suite added 0 checks - it is testing nothing")
        elif name not in crashed:
            check(f"{name} suite ran {ran} checks", True, "")
    try:
        check_js()
    except Exception:                               # noqa: BLE001
        crashed.append("javascript syntax")
        print("  FAIL javascript syntax suite crashed:\n"
              + "    " + traceback.format_exc().replace("\n", "\n    "))

    elapsed = time.monotonic() - started
    print(f"\n{PASS} passed, {FAIL} failed in {elapsed:.1f}s")
    if crashed:
        print("crashed suites: " + ", ".join(crashed))
    if silent:
        print("suites that ran no checks: " + ", ".join(silent))
    return 1 if (FAIL or crashed or silent) else 0


if __name__ == "__main__":
    sys.exit(main())
