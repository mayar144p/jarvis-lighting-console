"""sACN (E1.31) output - pure stdlib.

sACN is the streaming transport counterpart to Art-Net: both carry the
same 512-slot DMX frame, so Jarvis keeps ONE output abstraction - a
sender exposes send(universe, data) / stats() / close() / dry_run and
the engine never cares which wire protocol is underneath (see
ArtNetSender in app/artnet.py; this module mirrors its interface).

Packet layout (ANSI E1.31-2016), all multibyte fields BIG-endian:

  offset  size  field                        value
  0       2     Preamble Size                       0x0010
  2       2     Post-amble Size                     0x0000
  4       12    ACN Packet Identifier               b"ASC-E1.17\\0\\0\\0"
  --- Root Layer -----------------------------------------------------
  16      2     Root PDU Length (flags 0x7 + len)    0x7000 | L_root
  18      4     Root Layer Vector                   0x00000004 (DATA)
  22      16    CID (component id, unique per source)
  --- Framing Layer --------------------------------------------------
  38      2     Framing PDU Length (flags 0x7 + len) 0x7000 | L_frame
  40      4     Framing Layer Vector                 0x00000002 (DATA)
  44      64    Source Name (UTF-8, NUL padded)
  108     1     Priority (0-200, default 100)
  109     2     Synchronization Address (0 = none)
  111     1     Sequence Number (0-255, wraps - 0 IS valid)
  112     1     Options (bit7 preview, bit6 terminated, bit5 force sync)
  113     2     Destination Universe (1-63999)
  --- DMP Layer ------------------------------------------------------
  115     2     DMP PDU Length (flags 0x7 + len)     0x7000 | L_dmp
  117     1     DMP Layer Vector                     0x02
  118     1     Address Type & Data Type             0xA1
  119     2     First Property Address               0x0000
  121     2     Address Increment                    0x0001
  123     2     Property Value Count (1 + slots)
  125     1     DMX Start Code                       0x00
  126     n     DMX slot data (0-255 each)

Each PDU length counts itself: L_root = packet_len - 16,
L_frame = packet_len - 38, L_dmp = packet_len - 115, where
packet_len = 126 + slots.

Universe mapping matches the Art-Net path exactly (see
artnet.port_address): Art-Net Port-Address = (net << 8) + universe - 1,
so the sACN wire universe is simply Port-Address + 1 = (net << 8) +
universe.  Patch universe 1 @ net 0 -> sACN universe 1; universe 257
@ net 0 and universe 1 @ net 1 both -> 257, which is the SAME universe
Art-Net already maps them to.  Valid sACN universes are 1..63999.
"""
from __future__ import annotations

import socket
import struct
import uuid

E131_PORT = 5568
DMX_SLOTS = 512
PREAMBLE_SIZE = 0x0010
ACN_IDENTIFIER = b"ASC-E1.17\0\0\0"
ROOT_VECTOR = 0x00000004
FRAMING_VECTOR = 0x00000002
DMP_VECTOR = 0x02
DMP_TYPE = 0xA1
HEADER_SIZE = 126                    # bytes before the DMX slot data
UNIVERSE_MIN, UNIVERSE_MAX = 1, 63999
PRIORITY_MIN, PRIORITY_MAX = 0, 200
DEFAULT_PRIORITY = 100
DEFAULT_SOURCE_NAME = "JARVIS"
# flags nibble 0x7 = "sync address follows length field" per E1.31
_LENGTH_FLAGS = 0x7000


def _pdu_length(packet_len: int, layer_start: int) -> int:
    """Flags+length field for a layer: length counts itself, to packet end."""
    length = packet_len - layer_start
    if length > 0x0FFF:
        raise ValueError("sACN PDU too long")
    return _LENGTH_FLAGS | length


def sacn_universe(universe: int, net: int = 0) -> int:
    """Patch (universe, net) -> E1.31 wire universe (1..63999).

    Mirrors artnet.port_address: wire = port_address + 1.
    """
    try:
        u = int(universe)
        n = int(net)
    except (TypeError, ValueError):
        raise ValueError(f"bad universe/net: {universe!r}/{net!r}") from None
    if u < 1:
        raise ValueError("universes are 1-based")
    if not 0 <= n <= 127:
        raise ValueError("net must be 0..127")
    wire = (n << 8) + u
    if wire > UNIVERSE_MAX:
        raise ValueError(f"sACN universes run {UNIVERSE_MIN}..{UNIVERSE_MAX} "
                         f"(net {n} universe {u} -> {wire})")
    return wire


def default_cid() -> bytes:
    """Stable 16-byte component id for this installation.

    Receivers group packets by CID, so it must not change between
    restarts; uuid5 over hostname gives a deterministic value without
    storing state.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL,
                      f"jarvis-sacn://{socket.gethostname()}").bytes


def _cid_bytes(cid) -> bytes:
    if cid in (None, ""):
        return default_cid()
    if isinstance(cid, (bytes, bytearray)):
        raw = bytes(cid)
        if len(raw) != 16:
            raise ValueError("CID must be exactly 16 bytes")
        return raw
    text = str(cid).replace("-", "").strip()
    if len(text) != 32:
        raise ValueError("CID must be 32 hex characters (16 bytes)")
    try:
        return bytes.fromhex(text)
    except ValueError:
        raise ValueError("CID must be 32 hex characters (16 bytes)") from None


def _name_bytes(source_name) -> bytes:
    """UTF-8 source name, truncated on a character boundary, NUL padded."""
    text = str(source_name or DEFAULT_SOURCE_NAME)
    raw = text.encode("utf-8")[:63]            # 64-byte field, keep a NUL
    while raw:
        try:
            raw.decode("utf-8")
            break
        except UnicodeDecodeError:
            raw = raw[:-1]                     # cut mid-character: trim
    return raw.ljust(64, b"\0")


def multicast_group(universe: int, net: int = 0) -> str:
    """E1.31 multicast group for a 1-based patch universe."""
    wire = ((int(net) & 0x7F) << 8) + int(universe)
    wire = max(UNIVERSE_MIN, min(UNIVERSE_MAX, wire))
    return "239.255.%d.%d" % (wire >> 8, wire & 0xFF)


def build_sacn(universe: int, data, sequence: int = 0, net: int = 0,
               priority: int = DEFAULT_PRIORITY,
               source_name: str = DEFAULT_SOURCE_NAME, cid=None,
               options: int = 0, sync_address: int = 0) -> bytes:
    """Build one E1.31 DATA packet for a 1-based patch universe."""
    if len(data) < 1:
        raise ValueError("no DMX data")
    if len(data) > DMX_SLOTS:
        raise ValueError(f"a frame carries at most {DMX_SLOTS} slots")
    try:
        seq = int(sequence)
        prio = int(priority)
    except (TypeError, ValueError):
        raise ValueError(f"bad sequence/priority: {sequence!r}/{priority!r}") \
            from None
    if not 0 <= seq <= 255:
        raise ValueError("sequence must be 0..255")
    if not PRIORITY_MIN <= prio <= PRIORITY_MAX:
        raise ValueError(f"priority must be {PRIORITY_MIN}..{PRIORITY_MAX}")
    wire = sacn_universe(universe, net)

    slots = len(data)
    packet_len = HEADER_SIZE + slots
    buf = bytearray(packet_len)
    # envelope
    struct.pack_into(">HH", buf, 0, PREAMBLE_SIZE, 0)
    buf[4:16] = ACN_IDENTIFIER
    # root layer
    struct.pack_into(">H", buf, 16, _pdu_length(packet_len, 16))
    struct.pack_into(">I", buf, 18, ROOT_VECTOR)
    buf[22:38] = _cid_bytes(cid)
    # framing layer
    struct.pack_into(">H", buf, 38, _pdu_length(packet_len, 38))
    struct.pack_into(">I", buf, 40, FRAMING_VECTOR)
    buf[44:108] = _name_bytes(source_name)
    buf[108] = prio
    struct.pack_into(">H", buf, 109, int(sync_address) & 0xFFFF)
    buf[111] = seq
    buf[112] = int(options) & 0xFF
    struct.pack_into(">H", buf, 113, wire)
    # DMP layer
    struct.pack_into(">H", buf, 115, _pdu_length(packet_len, 115))
    buf[117] = DMP_VECTOR
    buf[118] = DMP_TYPE
    struct.pack_into(">H", buf, 119, 0)                 # first property
    struct.pack_into(">H", buf, 121, 1)                 # increment
    struct.pack_into(">H", buf, 123, slots + 1)         # start code + slots
    buf[125] = 0                                        # DMX start code
    buf[126:] = data
    return bytes(buf)


def decode_sacn(packet: bytes) -> dict:
    """Parse an E1.31 DATA packet (tests, loopback, and DMX input).

    Raises ValueError for anything that is not a well-formed unicast/
    multicast DATA packet with the DMX start code.
    """
    if len(packet) < HEADER_SIZE:
        raise ValueError("sACN packet too short")
    if packet[0:4] != struct.pack(">HH", PREAMBLE_SIZE, 0):
        raise ValueError("bad sACN preamble")
    if packet[4:16] != ACN_IDENTIFIER:
        raise ValueError("not an E1.31 packet")
    if struct.unpack_from(">I", packet, 18)[0] != ROOT_VECTOR:
        raise ValueError("not an E1.31 DATA packet")
    if struct.unpack_from(">I", packet, 40)[0] != FRAMING_VECTOR:
        raise ValueError("not an E1.31 DATA framing layer")
    if packet[117] != DMP_VECTOR or packet[118] != DMP_TYPE:
        raise ValueError("bad E1.31 DMP layer")
    count = struct.unpack_from(">H", packet, 123)[0]
    if count < 1:
        raise ValueError("empty E1.31 property value")
    if len(packet) < 125 + count:
        raise ValueError("truncated E1.31 property values")
    start_code = packet[125]
    if start_code != 0:
        raise ValueError(f"unsupported E1.31 start code 0x{start_code:02x}")
    data = bytes(packet[126:125 + count])
    options = packet[112]
    return {
        "universe": struct.unpack_from(">H", packet, 113)[0],
        "sequence": packet[111],
        "priority": packet[108],
        "sync_address": struct.unpack_from(">H", packet, 109)[0],
        "options": options,
        "preview": bool(options & 0x80),
        "stream_terminated": bool(options & 0x40),
        "source_name": packet[44:108].split(b"\0", 1)[0].decode("utf-8",
                                                                 "replace"),
        "cid": packet[22:38].hex(),
        "start_code": start_code,
        "data": data,
    }


SYNC_ROOT_VECTOR = 0x00000008       # VECTOR_ROOT_E131_EXTENDED
SYNC_FRAMING_VECTOR = 0x00000001    # VECTOR_E131_EXTENDED_SYNCHRONIZATION
SYNC_PACKET_SIZE = 49


def build_sync(sync_universe: int, sequence: int = 0, cid=None) -> bytes:
    """E1.31 Synchronization packet: receivers holding data that named
    this sync universe show it now."""
    if not UNIVERSE_MIN <= int(sync_universe) <= UNIVERSE_MAX:
        raise ValueError(f"a sync universe is {UNIVERSE_MIN}..{UNIVERSE_MAX}")
    buf = bytearray(SYNC_PACKET_SIZE)
    struct.pack_into(">HH", buf, 0, PREAMBLE_SIZE, 0)
    buf[4:16] = ACN_IDENTIFIER
    struct.pack_into(">H", buf, 16, _pdu_length(SYNC_PACKET_SIZE, 16))
    struct.pack_into(">I", buf, 18, SYNC_ROOT_VECTOR)
    buf[22:38] = _cid_bytes(cid)
    struct.pack_into(">H", buf, 38, _pdu_length(SYNC_PACKET_SIZE, 38))
    struct.pack_into(">I", buf, 40, SYNC_FRAMING_VECTOR)
    buf[44] = int(sequence) & 0xFF
    struct.pack_into(">H", buf, 45, int(sync_universe))
    return bytes(buf)                              # 47-48 reserved


class SacnSender:
    """sACN counterpart of ArtNetSender - same interface, same engine.

    dry_run counts frames but touches no socket.  Sequence numbers are
    kept per wire universe and wrap 0..255 (0 is a legal sACN sequence,
    unlike Art-Net where 0 means "disabled").
    """

    transport = "sacn"

    def __init__(self, host: str, port: int = E131_PORT, net: int = 0,
                 dry_run: bool = True, priority: int = DEFAULT_PRIORITY,
                 source_name: str = DEFAULT_SOURCE_NAME, cid=None,
                 sync_universe: int = 0):
        if not PRIORITY_MIN <= int(priority) <= PRIORITY_MAX:
            raise ValueError(f"sACN priority must be "
                             f"{PRIORITY_MIN}..{PRIORITY_MAX}")
        self.host = host
        self.port = int(port)
        self.net = int(net) & 0x7F
        self.dry_run = bool(dry_run)
        self.priority = int(priority)
        self.source_name = str(source_name or DEFAULT_SOURCE_NAME)
        self.cid = _cid_bytes(cid)
        # 0 = no synchronisation; else data names it and sync() fires it
        self.sync_universe = int(sync_universe) if UNIVERSE_MIN <= int(sync_universe or 0) <= UNIVERSE_MAX else 0
        self.syncs_sent = 0
        self._sync_seq = -1
        self.frames_sent = 0
        self.simulated_frames = 0
        self.errors = 0
        self.last_error: str | None = None
        self._sequences: dict[int, int] = {}
        self._sock: socket.socket | None = None

    # -- state -----------------------------------------------------------
    def next_sequence(self, universe: int) -> int:
        """Per-universe counter wrapping 0..255 (0 is a valid sequence)."""
        value = (self._sequences.get(universe, -1) + 1) % 256
        self._sequences[universe] = value
        return value

    def _socket(self) -> socket.socket:
        if self._sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 8)
            except OSError:
                pass                      # unicast targets need neither
            self._sock = sock
        return self._sock

    def destination(self, universe: int) -> str:
        """The address one universe is sent to.

        `multicast` (the default for sACN) is the E1.31 group for that
        universe, 239.255.<hi>.<lo>; anything else is a fixed unicast or
        broadcast host.
        """
        routed = (getattr(self, "routes", None) or {}).get(universe)
        if routed:
            return routed
        if self.host in ("multicast", ""):
            return multicast_group(universe, self.net)
        return self.host

    # -- sending ---------------------------------------------------------
    def send(self, universe: int, data) -> bool:
        """One frame for one universe. Returns True when it hit the wire."""
        if self.dry_run:
            self.simulated_frames += 1
            return False
        packet = build_sacn(universe, data, self.next_sequence(universe),
                            self.net, self.priority, self.source_name,
                            self.cid, sync_address=self.sync_universe)
        try:
            self._socket().sendto(packet,
                                  (self.destination(universe), self.port))
        except OSError as exc:
            self.errors += 1
            self.last_error = str(exc)
            print(f"[sacn] universe {universe} -> "
                  f"{self.destination(universe)}:{self.port} "
                  f"FAILED: {exc}")
            return False
        self.frames_sent += 1
        self.last_error = None
        return True

    def sync(self, universes: int) -> bool:
        """The E1.31 sync after a tick's frames, when a sync universe is
        set (the data then waits for it, so it goes every tick)."""
        if self.dry_run or not self.sync_universe or universes < 1:
            return False
        self._sync_seq = (self._sync_seq + 1) % 256
        dest = self.host if self.host not in ("multicast", "") else multicast_group(self.sync_universe, 0)
        try:
            self._socket().sendto(build_sync(self.sync_universe, self._sync_seq, self.cid), (dest, self.port))
        except OSError as exc:
            self.last_error = str(exc)
            return False
        self.syncs_sent += 1
        return True

    def stats(self) -> dict:
        return {
            "transport": self.transport,
            "sync_universe": self.sync_universe,
            "syncs_sent": self.syncs_sent,
            "host": f"{self.host}:{self.port}",
            "net": self.net,
            "priority": self.priority,
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
