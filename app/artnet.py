"""Art-Net (ArtDmx) output — pure stdlib.

Packet layout follows the Art-Net 4 specification (ArtDmx message):

  offset  size  field     value
  0       8     ID        b"Art-Net\\0"
  8       2     OpCode    0x5000 transmitted LITTLE-endian  ->  00 50
  10      2     ProtVer   protocol version 14, big-endian    ->  00 0E
  12      1     Sequence  1..255, one counter per universe (0 is never sent)
  13      1     Physical  0
  14      1     SubUni    low byte of the 15-bit Port-Address
  15      1     Net       high byte of the Port-Address (0-127)
  16      2     Length    big-endian slot count (512)
  18      512   Data      DMX channel values 0-255

Port-Address = Net<<8 | SubUni, composed of Net (7 bits), Sub-Net (4) and
Universe (4).  Jarvis patches universes 1-based while Art-Net is 0-based:
patch universe 1 -> Port-Address 0 -> SubUni 0.  DMX_NET shifts the whole
patch up by Net; universes beyond 256 carry into the next Net.

The packet bytes are verified against this table in tools/selftest.py and
decoded again end-to-end by tools/artnet_loopback.py.
"""
from __future__ import annotations

import socket
import struct
import time

ART_NET_PORT = 6454
ARTDMX_OP = 0x5000
PROTOCOL_VERSION = 14
DMX_SLOTS = 512
NET_MASK = 0x7F


def port_address(universe: int, net: int = 0) -> tuple[int, int]:
    """Patch universe (1-based) + configured Net -> (SubUni, Net) bytes."""
    if universe < 1:
        raise ValueError("universes are 1-based")
    address = ((int(net) & NET_MASK) << 8) + (universe - 1)
    return address & 0xFF, (address >> 8) & NET_MASK


def build_artdmx(universe: int, data, sequence: int, net: int = 0,
                 physical: int = 0) -> bytes:
    """Build one ArtDmx datagram payload for a 1-based patch universe."""
    if len(data) < 1:
        raise ValueError("no DMX data")
    if len(data) > DMX_SLOTS:
        raise ValueError(f"a frame carries at most {DMX_SLOTS} slots")
    subuni, net_byte = port_address(universe, net)
    buf = bytearray(18 + len(data))
    buf[0:8] = b"Art-Net\0"
    struct.pack_into("<H", buf, 8, ARTDMX_OP)          # 00 50
    struct.pack_into(">H", buf, 10, PROTOCOL_VERSION)   # 00 0E
    buf[12] = int(sequence) & 0xFF
    buf[13] = int(physical) & 0xFF
    buf[14] = subuni
    buf[15] = net_byte
    struct.pack_into(">H", buf, 16, len(data))
    buf[18:] = data
    return bytes(buf)


ARTSYNC_OP = 0x5200


def build_artsync() -> bytes:
    """ArtSync: every node shows the ArtDmx frames it holds at once, so a
    chase across several universes moves as one."""
    buf = bytearray(14)
    buf[0:8] = b"Art-Net\0"
    struct.pack_into("<H", buf, 8, ARTSYNC_OP)
    struct.pack_into(">H", buf, 10, PROTOCOL_VERSION)
    return bytes(buf)                              # Aux1, Aux2 = 0


def is_broadcast(host: str) -> bool:
    return host == "255.255.255.255" or host.endswith(".255")


def decode_artdmx(packet: bytes) -> dict:
    """Parse an ArtDmx packet (used by the loopback tool and tests)."""
    if len(packet) < 18 or packet[0:8] != b"Art-Net\0":
        raise ValueError("not an Art-Net packet")
    if struct.unpack_from("<H", packet, 8)[0] != ARTDMX_OP:
        raise ValueError("not an ArtDmx packet")
    length = struct.unpack_from(">H", packet, 16)[0]
    return {
        "version": struct.unpack_from(">H", packet, 10)[0],
        "sequence": packet[12],
        "physical": packet[13],
        "subuni": packet[14],
        "net": packet[15],
        "port_address": (packet[15] << 8) | packet[14],
        "length": length,
        "data": packet[18:18 + length],
    }


# ---- discovery: ArtPoll / ArtPollReply ---------------------------------
# Discovery reuses the same 10-byte ID+OpCode header as ArtDmx.  Unlike
# ArtDmx, ArtPollReply carries no ProtVer - its body starts at offset 10
# (Wireshark packet-artnet.c skips ProtVer only for POLL_REPLY and
# POLL_FP_REPLY; ArtPoll itself DOES have it at offset 10).
# OpPoll = 0x2000 per the Art-Net 4 spec (Table 1), transmitted
# little-endian -> wire bytes 00 20.  A byte-swapped 0x0020 would read
# back as an unassigned OpCode and be dropped by spec-compliant nodes,
# so no real hardware would ever answer our discovery.
ARTPOLL_OP = 0x2000
ARTPOLLREPLY_OP = 0x2100
# ID(8) + OpCode(2) + ProtVer(2) + TalkToMe(1) + Priority(1)
POLL_SIZE = 14


def build_artpollreply(ip: str = "192.168.0.50", port: int = ART_NET_PORT,
                       net: int = 0, sub: int = 0, oem: int = 0x7F50,
                       version: int = 14, name: str = "Test Node",
                       ports: tuple[int, ...] = (0, 1, 2, 3)) -> bytes:
    """A well-formed ArtPollReply, laid out exactly as parse_artpollreply
    documents it (offsets verified against Wireshark packet-artnet.c).

    This exists so the discovery path can be tested against a node that
    answers the way real hardware does.  Without it, "the sweep finds
    nodes" would be an untested claim - and the sweep is the one piece of
    this that has to work on a network where broadcast is filtered.
    """
    def _pad(text, size: int) -> bytes:
        raw = str(text or "").encode("ascii", "replace")[:size]
        return raw + b"\0" * (size - len(raw))

    try:
        octets = [max(0, min(255, int(p))) for p in str(ip).split(".")[:4]]
        while len(octets) < 4:
            octets.append(0)
    except (TypeError, ValueError):
        octets = [0, 0, 0, 0]

    p = bytearray(240)
    p[0:8] = b"Art-Net\0"
    struct.pack_into("<H", p, 8, ARTPOLLREPLY_OP)
    p[10:14] = bytes(octets)                       # IpAddress
    struct.pack_into("<H", p, 14, port & 0xFFFF)   # Port (LE)
    struct.pack_into(">H", p, 16, version & 0xFFFF)
    p[18] = net & 0x7F                              # NetSwitch
    p[19] = sub & 0x0F                              # SubSwitch
    struct.pack_into(">H", p, 20, oem & 0xFFFF)
    struct.pack_into("<H", p, 24, 0)               # EstaMan (LE)
    p[26:44] = _pad(name, 18)                       # ShortName
    p[44:108] = _pad(f"{name} long", 64)           # LongName
    p[108:172] = _pad("#0001 [0002] Ok", 64)        # NodeReport
    struct.pack_into(">H", p, 172, min(len(ports), 4))   # NumPorts
    for i in range(4):                             # PortTypes
        p[174 + i] = 0x80 if i < len(ports) else 0x00
    for i in range(4):                             # GoodInput
        struct.pack_into(">H", p, 178 + i * 2, 0x8000 if i < len(ports) else 0)
    for i in range(4):                             # GoodOutput
        struct.pack_into(">H", p, 182 + i * 2, 0x8000 if i < len(ports) else 0)
    for i in range(4):                             # SwIn
        p[186 + i] = 0
    for i in range(4):                             # SwOut: the port's sub-net
        p[190 + i] = (ports[i] & 0x0F) if i < len(ports) else 0
    p[200] = 0x01                                   # Style: node
    p[201:207] = bytes([0x02, 0x79, 0x80, 0x74, 0xD9, 0xF0])   # MAC
    return bytes(p)


def build_artpoll(talk_to_me: int = 0, priority: int = 0) -> bytes:
    """Build the 14-byte ArtPoll discovery broadcast.

      offset  size  field     value
      0       8     ID        b"Art-Net\\0"
      8       2     OpCode    0x2000 (OpPoll) transmitted LITTLE-endian
                              -> wire bytes 00 20
      10      2     ProtVer   protocol version 14, big-endian  -> 00 0E
      12      1     TalkToMe  bit 0 unused; 0x02 = nodes may also send
                              ArtPollReply when conditions change;
                              0 = plain "reply when polled" (most
                              compatible - some nodes ignore 0x02)
      13      1     Priority  minimum diagnostics priority (0 = low)

    Nodes answer with ArtPollReply within 3 s, so scan() polls twice.
    This is the ONLY packet discovery puts on the wire - no DMX data
    ever leaves the machine during a scan.
    """
    buf = bytearray(POLL_SIZE)
    buf[0:8] = b"Art-Net\0"
    struct.pack_into("<H", buf, 8, ARTPOLL_OP)          # 00 20
    struct.pack_into(">H", buf, 10, PROTOCOL_VERSION)    # 00 0E
    buf[12] = int(talk_to_me) & 0xFF
    buf[13] = int(priority) & 0xFF
    return bytes(buf)


def _ascii(buf: bytes, start: int, size: int) -> str:
    """Fixed-width NUL-terminated ASCII field (names, node report)."""
    return buf[start:start + size].split(b"\0", 1)[0].decode("ascii", "replace")


def parse_artpollreply(packet: bytes) -> dict | None:
    """Parse an ArtPollReply.  Returns None when the packet is not one.

    Field offsets (verified against Wireshark packet-artnet.c; body
    starts at offset 10 - no ProtVer in this message):

      10  4  IP           14  2  Port(LE)    16  2  VersInfo(BE)
      18  1  NetSwitch    19  1  SubSwitch   20  2  Oem(BE)
      24  2  EstaMan(LE)  26  18 ShortName   44  64 LongName
      108 64 NodeReport   172 2  NumPorts    174 4  PortTypes
      178 4  GoodInput    182 4  GoodOutput  186 4  SwIn
      190 4  SwOut        200 1  Style       201 6  MAC

    Port-Address of output port i (Art-Net 4):
        (NetSwitch & 0x7F) << 8 | (SubSwitch & 0x0F) << 4 | (SwOut[i] & 0x0F)
    PortTypes[i]: 0x80 = output, 0x40 = input, 0xC0 = both (low 3 bits
    = protocol, 0x00 = DMX512), so `& 0x80` selects output-capable ports.
    Truncated packets parse as far as they reach.
    """
    if len(packet) < 16 or packet[0:8] != b"Art-Net\0":
        return None
    if struct.unpack_from("<H", packet, 8)[0] != ARTPOLLREPLY_OP:
        return None
    net = packet[18] & 0x7F if len(packet) >= 19 else 0
    sub = packet[19] & 0x0F if len(packet) >= 20 else 0
    reply: dict = {
        "ip": ".".join(str(b) for b in packet[10:14]),
        "port": struct.unpack_from("<H", packet, 14)[0],
        "net": net,
        "sub": sub,
        "output_ports": [],
    }
    if len(packet) >= 18:
        reply["version"] = struct.unpack_from(">H", packet, 16)[0]
    if len(packet) >= 22:
        reply["oem"] = struct.unpack_from(">H", packet, 20)[0]
    if len(packet) >= 44:
        reply["name"] = _ascii(packet, 26, 18)
    if len(packet) >= 108:
        reply["long_name"] = _ascii(packet, 44, 64)
    if len(packet) >= 174:
        reply["num_ports"] = min(struct.unpack_from(">H", packet, 172)[0], 4)
    if len(packet) >= 194:
        port_types, swout = packet[174:178], packet[190:194]
        for i in range(min(reply.get("num_ports", 4), 4)):
            if port_types[i] & 0x80:               # output-capable port
                reply["output_ports"].append(
                    (net << 8) | (sub << 4) | (swout[i] & 0x0F))
    if len(packet) >= 201:
        reply["style"] = packet[200]
    if len(packet) >= 207:
        reply["mac"] = ":".join(f"{b:02x}" for b in packet[201:207])
    return reply


def universe_from_port_address(port_address: int, net: int = 0) -> int:
    """Map an observed Port-Address back to a 1-based patch universe.

    The sender maps (universe, net) -> port_address = (net & 0x7F) << 8
    + universe - 1 (see port_address()); this is its inverse.  With the
    default net 0 it is simply port_address + 1.  A node living below
    the configured net cannot be reached at our output address anyway
    (adjust DMX_NET), but it is still returned at its raw position
    instead of being silently dropped.
    """
    universe = int(port_address) - ((int(net) & NET_MASK) << 8) + 1
    return universe if universe >= 1 else int(port_address) + 1


class _Probe:
    """The sockets a discovery pass listens on.

    Two, because Art-Net gear is inconsistent about where a reply lands:

      * `src` - an EPHEMERAL port.  The spec says an ArtPollReply is
        unicast back to the address:port the ArtPoll came from, so this
        catches every compliant node with no chance of a port collision.
      * `canon` - the canonical udp/6454, bound WITHOUT SO_REUSEADDR so
        we own it exclusively.  Some nodes, bridges and software nodes
        instead send to the fixed port; if nobody else holds it we still
        see that traffic.  If the bind fails, another controller already
        owns 6454 and the datagrams would go to it regardless - so we
        correctly skip rather than silently receiving nothing (which is
        exactly what SO_REUSEADDR would have caused: the bind SUCCEEDS,
        the reply is delivered to the other socket, and discovery reports
        an empty network while the node is answering perfectly well).
    """

    def __init__(self, port: int) -> None:
        self.socks: list[socket.socket] = []
        self.canon_owned = False
        self.canon_shared = False
        self.src_port = 0
        self._open(port)

    def _open(self, port: int) -> None:
        src = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            src.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        try:
            src.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        except OSError:
            pass
        src.bind(("", 0))
        self.src_port = src.getsockname()[1]
        self.socks.append(src)
        # The canonical port, best effort.  First try to OWN it outright
        # (no SO_REUSEADDR) so nothing can steal the datagrams; if someone
        # already holds it, share it as a fallback, because gear that
        # broadcasts its reply to the fixed port is otherwise invisible.
        # That fallback is best-effort by nature: Windows hands a unicast
        # datagram to only one socket sharing a port, which is precisely
        # why the ephemeral socket above is the primary path.
        canon = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            canon.bind(("", port))
            self.canon_owned = True
        except OSError:
            try:
                canon.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                canon.bind(("", port))
                self.canon_shared = True
            except OSError:
                canon.close()
                return
        try:
            canon.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        try:
            canon.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        except OSError:
            pass
        self.socks.append(canon)

    def poll(self, deadline: float) -> tuple[bytes, tuple] | None:
        """Next datagram before `deadline`, or None on timeout/close."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            import select
            ready, _, _ = select.select(self.socks, [], [],
                                        min(0.2, remaining))
        except (OSError, ValueError):
            return None
        for s in ready:
            try:
                return s.recvfrom(2048)
            except ConnectionResetError:
                continue           # ICMP from a host with no Art-Net port
            except (BlockingIOError, socket.timeout, TimeoutError):
                continue
            except OSError:
                continue
        return None

    def close(self) -> None:
        for s in self.socks:
            try:
                s.close()
            except OSError:
                pass
        self.socks = []


def local_subnets(limit: int = 4) -> list[str]:
    """Local IPv4 /24 prefixes to sweep, most-likely first.

    Learned without any external dependency: a connected UDP socket has
    to be routed by the OS, so getsockname() reports the address the OS
    picked for that route.  Connecting to the address itself sends
    nothing (UDP connect is local), so this is free and silent.
    """
    found: list[str] = []
    try:                                    # every adapter, 2.x included
        from . import netif
        for row in netif.interfaces():
            parts = row["ip"].split(".")
            prefix = ".".join(parts[:3]) + "."
            if len(parts) == 4 and prefix not in found:
                found.append(prefix)
    except Exception:                       # discovery is best-effort
        pass
    if len(found) >= limit:
        return found[:limit]
    probes = ["2.0.0.1", "192.168.0.1", "10.0.0.1", "172.16.0.1", "8.8.8.8"]
    for target in probes:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.2)
            s.connect((target, 9))          # no packet is transmitted
            local = s.getsockname()[0]
        except OSError:
            continue
        finally:
            s.close()
        if local.startswith("127."):
            continue
        parts = local.split(".")
        if len(parts) != 4:
            continue
        prefix = ".".join(parts[:3]) + "."
        if prefix not in found:
            found.append(prefix)
        if len(found) >= limit:
            break
    return found


def sweep(subnets: list[str] | None = None, timeout: float = 2.0,
          port: int = ART_NET_PORT, net: int = 0,
          per_address: bool = False) -> dict:
    """Find Art-Net nodes by UNICAST ArtPoll, one address at a time.

    Why this exists: `scan()` broadcasts, and broadcast is routinely
    dropped on Wi-Fi (AP filtering, client isolation) - and when it is,
    nothing on the network says so.  The app then reports "no traffic
    observed" and the operator concludes the rig is broken, when in fact
    the node is answering perfectly well to unicast.  A node that ignores
    a broadcast still answers its own IP, so sweeping the subnet is the
    difference between "definitely absent" and "probably filtered".

    All polls go out in one burst and the socket then listens for the
    whole window: a node answers in milliseconds, so there is no need to
    wait per address.  That keeps a 254-address /24 at roughly the same
    wall-clock cost as a single broadcast scan.

    Returns the same shape as `scan()` plus `swept` (how many addresses
    were polled) and `subnets`, so a caller can report exactly what was
    tried when nothing is found.
    """
    t_start = time.monotonic()
    if subnets is None:
        subnets = local_subnets()
    t_subnets = time.monotonic() - t_start
    subnets = [s for s in subnets if s]
    targets: list[str] = []
    for prefix in subnets:
        for last in range(1, 255):
            targets.append(f"{prefix}{last}")
    if not targets:
        return {"universes": [], "nodes": [], "error": None,
                "polls_sent": 0, "replies": 0, "frames": 0,
                "swept": 0, "subnets": [],
                "message": "no local IPv4 network to sweep"}

    bound_port = 0
    bind_note = ""
    sent = unreachable = 0
    try:
        # See _Probe: poll from an ephemeral port (spec-compliant replies
        # come back to the source) and additionally own udp/6454 outright
        # when possible, for gear that sends there instead.
        probe = _Probe(port)
        bound_port = probe.src_port

        poll = build_artpoll()
        # The polls go out without waiting: a send to an address nobody
        # owns can block while the computer asks the network who has it
        # (6 s for 508 addresses on GitHub's Ubuntu runners), and "Find
        # nodes" would sit there.  What can't go straight out is skipped,
        # and the whole burst gets a second at most.
        sock = probe.socks[0]
        sock.setblocking(False)
        send_until = time.monotonic() + 1.0
        try:
            for target in targets:
                if time.monotonic() > send_until:
                    break
                # the queue full: let it drain and send THIS address again,
                # for as long as the burst's second lasts (skipping it
                # missed a node now and then - and the CI's "how hard it
                # looked" count came out 252 of 254; with 20 tries 1 ms
                # apart a busy machine still dropped a dozen, 242)
                while True:
                    try:
                        sock.sendto(poll, (target, port))
                        sent += 1
                        break
                    except BlockingIOError:
                        if time.monotonic() > send_until:
                            break
                        time.sleep(0.001)
                    except OSError:
                        # "host unreachable": the computer already knows
                        # nobody is there - the address WAS looked at
                        # (dropping these made the CI's count 244 of 254)
                        unreachable += 1
                        break
                if per_address:
                    time.sleep(0.002)
        finally:
            sock.setblocking(True)

        t_sent = time.monotonic() - t_start - t_subnets
        universes: dict[int, dict] = {}
        nodes: dict[str, dict] = {}
        node_by_ip: dict[str, str] = {}
        replies = frames = 0
        deadline = time.monotonic() + max(0.2, float(timeout))
        try:
            while True:
                if time.monotonic() >= deadline:
                    break
                got = probe.poll(deadline)
                if got is None:
                    continue
                data, addr = got
                if len(data) < 10 or data[0:8] != b"Art-Net\0":
                    continue
                opcode = struct.unpack_from("<H", data, 8)[0]
                if opcode == ARTPOLLREPLY_OP:
                    reply = parse_artpollreply(data)
                    if reply is None:
                        continue
                    replies += 1
                    name = reply.get("name") or reply["ip"]
                    node_by_ip.setdefault(reply["ip"], name)
                    node = nodes.setdefault(name, {
                        "name": reply.get("name", ""),
                        "long_name": reply.get("long_name", ""),
                        "ip": reply["ip"], "port": reply["port"],
                        "mac": reply.get("mac", ""),
                        "oem": reply.get("oem", 0),
                        "version": reply.get("version", 0),
                        "output_ports": [],
                    })
                    for pa in reply.get("output_ports", ()):
                        if pa not in node["output_ports"]:
                            node["output_ports"].append(pa)
                        row = universes.setdefault(pa, {
                            "port_address": pa, "frames": 0, "channels": 0,
                            "announced": True, "src": "", "node": name,
                        })
                        row["announced"] = True
                        row["node"] = row["node"] or name
                elif opcode == ARTDMX_OP:
                    try:
                        frame = decode_artdmx(data)
                    except ValueError:
                        continue
                    frames += 1
                    depth = 0
                    slots = frame["data"]
                    for idx in range(len(slots) - 1, -1, -1):
                        if slots[idx]:
                            depth = idx + 1
                            break
                    row = universes.setdefault(frame["port_address"], {
                        "port_address": frame["port_address"], "frames": 0,
                        "channels": 0, "announced": False, "src": "", "node": "",
                    })
                    row["frames"] += 1
                    row["channels"] = max(row["channels"], depth)
                    row["src"] = addr[0]
        finally:
            probe.close()
    except OSError as exc:
        return {"universes": [], "nodes": [], "error": str(exc),
                "polls_sent": sent, "replies": 0, "frames": 0,
                "swept": sent + unreachable, "unreachable": unreachable, "subnets": subnets}

    for row in universes.values():
        if not row["node"]:
            row["node"] = node_by_ip.get(row["src"], "")
    rows = []
    for pa in sorted(universes):
        row = universes[pa]
        rows.append({
            "universe": universe_from_port_address(pa, net),
            "port_address": pa,
            "frames": row["frames"],
            "channels": row["channels"],
            "announced": row["announced"],
            "node": row["node"],
            "via": "unicast sweep",
        })
    # addresses the burst's second ran out on (a computer too busy to send
    # them): said, never counted as looked at
    skipped = max(0, len(targets) - sent - unreachable)
    message = (f"{len(nodes)} node(s), {len(rows)} universe(s) on "
               f"{sent + unreachable} polled address(es)" if nodes else
               f"no node answered on {sent + unreachable} unicast address(es) "
               f"({', '.join(subnets)})") + (
               f"; {skipped} address(es) not polled (the computer was too busy) - try again" if skipped else "")
    return {"universes": rows, "nodes": list(nodes.values()), "error": None,
            "polls_sent": sent, "replies": replies, "frames": frames,
            "swept": sent + unreachable, "unreachable": unreachable, "skipped": skipped,
            "subnets": subnets, "bound_port": bound_port,
            "bind_note": bind_note, "message": message,
            # seconds per stage, to see where a slow "Find nodes" went
            "took": {"subnets": round(t_subnets, 2), "send": round(t_sent, 2),
                     "listen": round(time.monotonic() - t_start - t_subnets - t_sent, 2)}}


def scan(timeout: float = 2.0, port: int = ART_NET_PORT,
         net: int = 0, sweep_subnets: bool = True,
         sweep_timeout: float = 1.5,
         targets: list[str] | None = None) -> dict:
    """Sniff the wire for the rig: ArtPollReply nodes + live ArtDmx.

    Broadcasts ArtPoll and listens for `timeout` seconds, merging two
    signals:
      * ArtDmx frames  -> the universe carries data; the highest non-zero
        slot is the observed depth (channels actually used),
      * ArtPollReply   -> a node announces its output Port-Addresses even
        when idle (depth 0 -> auto-patch sizes one head there).

    A broadcast that finds nothing is NOT evidence that no node exists:
    Wi-Fi APs and client isolation routinely drop it, silently.  So when
    the broadcast pass comes up empty, `sweep_subnets` (on by default)
    falls back to unicast across the local /24s and the two results are
    merged.  The returned `tried` says exactly what was attempted, so a
    negative result is a real negative rather than a shrug.

    Discovery only: no DMX is transmitted.  Binding udp/6454 may fail
    while another controller holds it exclusively (on Windows
    SO_REUSEADDR usually still allows sharing the port); a bind failure
    degrades to an empty result with "error" set instead of raising -
    this is best-effort discovery, never a hard requirement.

    Returns {"universes": [{"universe", "port_address", "frames",
    "channels", "via", "node"}, ...] sorted by port address (universe
    already mapped 1-based through `net`), "nodes": [...], "error":
    str | None, "polls_sent", "replies", "frames"}.

    `targets` are extra poll destinations: each adapter's own directed
    broadcast (255.255.255.255 leaves by the default route only, so a
    laptop on Wi-Fi and a 2.x lighting network would otherwise never
    poll the lighting side) and any node IP already known.
    """
    timeout = max(0.2, float(timeout))
    universes: dict[int, dict] = {}
    nodes: dict[str, dict] = {}
    node_by_ip: dict[str, str] = {}
    polls_sent = replies = frames = 0

    try:
        # _Probe: poll from an ephemeral port (spec-compliant replies come
        # back to the source) and additionally own udp/6454 outright when
        # possible, so gear that sends there instead is still heard.
        probe = _Probe(port)
    except OSError as exc:
        message = f"cannot open a UDP socket: {exc}"
        print(f"[artnet] scan {message}")
        return {"universes": [], "nodes": [], "error": message,
                "polls_sent": 0, "replies": 0, "frames": 0}

    poll = build_artpoll()

    def _poll(target: str) -> None:
        nonlocal polls_sent
        try:
            probe.socks[0].sendto(poll, (target, port))
            polls_sent += 1
        except OSError:                 # broadcast blocked -> sniff anyway
            pass

    def _row(pa: int) -> dict:
        return universes.setdefault(pa, {
            "port_address": pa, "frames": 0, "channels": 0,
            "announced": False, "src": "", "node": "",
        })

    extra = [t for t in dict.fromkeys(targets or ())
             if t and t not in ("255.255.255.255", "127.0.0.1")]
    deadline = time.monotonic() + timeout
    _poll("255.255.255.255")
    _poll("127.0.0.1")                  # local software nodes / bridges
    for target in extra:
        _poll(target)
    repoll_at = time.monotonic() + min(timeout / 2.0, 1.5)
    repolled = False
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            if not repolled and now >= repoll_at:
                repolled = True
                _poll("255.255.255.255")   # nodes may take up to 3 s
                for target in extra:
                    _poll(target)
            got = probe.poll(deadline)
            if got is None:
                continue
            data, addr = got
            if len(data) < 10 or data[0:8] != b"Art-Net\0":
                continue
            opcode = struct.unpack_from("<H", data, 8)[0]
            if opcode == ARTPOLLREPLY_OP:
                reply = parse_artpollreply(data)
                if reply is None:
                    continue
                replies += 1
                name = reply.get("name") or reply["ip"]
                node_by_ip.setdefault(reply["ip"], name)
                node = nodes.setdefault(name, {
                    "name": reply.get("name", ""),
                    "long_name": reply.get("long_name", ""),
                    "ip": reply["ip"], "port": reply["port"],
                    "mac": reply.get("mac", ""),
                    "oem": reply.get("oem", 0),
                    "version": reply.get("version", 0),
                    "output_ports": [],
                })
                for pa in reply.get("output_ports", ()):
                    if pa not in node["output_ports"]:
                        node["output_ports"].append(pa)
                    row = _row(pa)
                    row["announced"] = True
                    row["node"] = row["node"] or name
            elif opcode == ARTDMX_OP:
                try:
                    frame = decode_artdmx(data)
                except ValueError:
                    continue
                frames += 1
                depth = 0
                slots = frame["data"]
                for idx in range(len(slots) - 1, -1, -1):   # last used slot
                    if slots[idx]:
                        depth = idx + 1                    # 1-based channel
                        break
                row = _row(frame["port_address"])
                row["frames"] += 1
                row["channels"] = max(row["channels"], depth)
                row["src"] = addr[0]
    finally:
        probe.close()

    # Attribute sniffed ArtDmx to the node that announced its IP.
    for row in universes.values():
        if not row["node"]:
            row["node"] = node_by_ip.get(row["src"], "")
    rows = []
    for pa in sorted(universes):
        row = universes[pa]
        if row["frames"]:
            via = "both" if row["announced"] else "artdmx"
        else:
            via = "artpoll"
        rows.append({
            "universe": universe_from_port_address(pa, net),
            "port_address": pa,
            "frames": row["frames"],
            "channels": row["channels"],
            "via": via,
            "node": row["node"],
        })
    tried = ["broadcast", "loopback"] + extra
    polled = polls_sent

    # Broadcast came up empty: that is not proof of absence, because Wi-Fi
    # AP filtering and client isolation drop it silently.  Sweep the local
    # /24s by unicast before reporting a negative.
    swept = 0
    subnets: list[str] = []
    took: dict = {"broadcast": round(time.monotonic() - (deadline - timeout), 2)}
    if sweep_subnets and not rows:
        tried.append("unicast sweep")
        t_sweep = time.monotonic()
        found = sweep(timeout=sweep_timeout, port=port, net=net)
        took.update(found.get("took") or {}, sweep_total=round(time.monotonic() - t_sweep, 2))
        # count the sweep's polls whether or not anything answered: a
        # negative result is only meaningful if we say how hard we looked
        # (an address the computer itself called unreachable was looked at too)
        swept = int(found.get("swept") or found.get("polls_sent") or 0)
        subnets = found.get("subnets") or []
        if found.get("nodes") or found.get("universes"):
            for row in found["universes"]:
                if row["port_address"] not in universes:
                    rows.append(row)
            for node in found["nodes"]:
                nodes.setdefault(node["name"] or node["ip"], node)
            replies += found["replies"]
            frames += found["frames"]
            tried.append("unicast sweep found it")

    if rows:
        rows.sort(key=lambda r: r["port_address"])
        message = (f"{len(nodes)} node(s), {len(rows)} universe(s) observed")
    elif swept:
        where = ", ".join(s.rstrip(".") for s in subnets) or "the local network"
        message = (f"no Art-Net node answered.  Tried broadcast, loopback and "
                   f"{swept} unicast address(es) on {where}.  If the rig is "
                   f"powered, check: node power, the network cable, and "
                   f"that the node's Art-Net port is enabled.")
    else:
        message = ("no Art-Net traffic observed (broadcast only - no local "
                   "network to sweep)")
    return {"universes": rows, "nodes": list(nodes.values()), "error": None,
            "polls_sent": polled + swept, "replies": replies, "frames": frames,
            "tried": tried, "swept": swept, "subnets": subnets, "took": took,
            "message": message}


class ArtNetSender:
    """Sends ArtDmx frames. dry_run counts frames but touches no socket."""

    def __init__(self, host: str, port: int = ART_NET_PORT, net: int = 0,
                 dry_run: bool = True):
        self.host = host
        self.port = int(port)
        self.net = int(net) & NET_MASK
        self.dry_run = bool(dry_run)
        self.frames_sent = 0
        self.simulated_frames = 0
        self.errors = 0
        self.syncs_sent = 0
        self.last_error: str | None = None
        self._sequences: dict[int, int] = {}
        self._sock: socket.socket | None = None

    # -- state -----------------------------------------------------------
    def next_sequence(self, universe: int) -> int:
        """Per-universe counter cycling 1..255 (0 means 'disabled')."""
        value = (self._sequences.get(universe, 0) % 255) + 1
        self._sequences[universe] = value
        return value

    def _socket(self) -> socket.socket:
        if self._sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass                      # unicast targets need no broadcast flag
            self._sock = sock
        return self._sock

    # -- sending ---------------------------------------------------------
    def send(self, universe: int, data) -> bool:
        """One frame for one universe. Returns True when it hit the wire."""
        if self.dry_run:
            self.simulated_frames += 1
            return False
        packet = build_artdmx(universe, data, self.next_sequence(universe), self.net)
        host = (getattr(self, "routes", None) or {}).get(universe, self.host)
        try:
            self._socket().sendto(packet, (host, self.port))
        except OSError as exc:
            self.errors += 1
            self.last_error = str(exc)
            print(f"[artnet] universe {universe} -> {self.host}:{self.port} "
                  f"FAILED: {exc}")
            return False
        self.frames_sent += 1
        self.last_error = None
        return True

    def sync(self, universes: int) -> bool:
        """ArtSync after a tick's frames.  Only when more than one universe
        went out (one universe is already in step with itself), and never
        to a broadcast address: Art-Net 4 leaves broadcast ArtDmx
        unsynchronised, and a node that got a sync it can't pair would
        hold its frames."""
        if self.dry_run or universes < 2 or is_broadcast(self.host):
            return False
        try:
            self._socket().sendto(build_artsync(), (self.host, self.port))
        except OSError as exc:
            self.last_error = str(exc)
            return False
        self.syncs_sent += 1
        return True

    def stats(self) -> dict:
        return {
            "transport": "artnet",
            "syncs_sent": self.syncs_sent,
            "host": f"{self.host}:{self.port}",
            "net": self.net,
            "frames_sent": self.frames_sent,
            "simulated_frames": self.simulated_frames,
            "errors": self.errors,
            "last_error": self.last_error,
        }

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
