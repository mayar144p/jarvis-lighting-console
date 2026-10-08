"""Shared by every self-test part: check(), the pass / fail counts, the
sample GDTF files and the helpers more than one part uses."""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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


def engine_source() -> str:
    """The engine's source: app/engine.py and its parts (engine_*.py)."""
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "app").glob("engine*.py")))


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}  {detail}")


def _valueerror(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def _free_udp_port() -> int:
    """A UDP port for a test, from 20000-29999: below the range the system
    hands out at random (32768+ on Linux, 49152+ on Windows), so nothing
    else running - a desk, a browser, the screen check - grabs it between
    this check and the test's own bind.  Both it and the next port are
    free (the CDJ listener takes port + 1 for the players' status)."""
    import random as _random
    import socket as _socket
    for _ in range(200):
        port = _random.randint(20000, 29998)
        socks = []
        try:
            for p in (port, port + 1):
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
                socks.append(s)
                s.bind(("", p))
            return port
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
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


def _raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def _which(prog: str) -> str | None:
    from shutil import which
    return which(prog)
