"""RDM over Art-Net: ask the lights themselves what they are.

A node that supports RDM keeps a Table of Devices (ToD) per output port:
the UIDs of the RDM fixtures on that cable.  Discovery here is:

    ArtTodRequest (0x8000)  -> node answers ArtTodData (0x8100) with UIDs
    ArtRdm (0x8300) GET DEVICE_INFO / MANUFACTURER_LABEL /
        DEVICE_MODEL_DESCRIPTION / DEVICE_LABEL per UID
                            -> node relays each fixture's RDM response

which gives, per light: who made it, the model, its current DMX start
address, footprint (channels) and personality (mode).  SET
DMX_START_ADDRESS readdresses a light from the desk.

Pure standard library.  Best effort, like artnet.scan: a node without RDM
simply never answers, and the result says what was tried.  Nothing here
touches engine state; engine.rdm_compare lines the answers up with the
patch.

Packet layouts follow the Art-Net 4 spec (ArtTodRequest / ArtTodData /
ArtRdm) and ANSI E1.20 (the RDM message inside ArtRdm, which carries it
WITHOUT the 0xCC start code; the checksum still counts it).
"""
from __future__ import annotations

import os
import struct
import time

from app import artnet

TODREQUEST_OP = 0x8000
TODDATA_OP = 0x8100
RDM_OP = 0x8300
RDM_VERSION = 0x01

SC_RDM = 0xCC
SUB_START = 0x01
GET, GET_RESPONSE, SET, SET_RESPONSE = 0x20, 0x21, 0x30, 0x31
ACK = 0x00

PID_DEVICE_INFO = 0x0060
PID_DEVICE_MODEL_DESCRIPTION = 0x0080
PID_MANUFACTURER_LABEL = 0x0081
PID_DEVICE_LABEL = 0x0082
PID_DMX_PERSONALITY_DESCRIPTION = 0x00E1
PID_DMX_START_ADDRESS = 0x00F0

# Our controller UID: 0x7FF0-0x7FFF is the manufacturer range E1.20 sets
# aside for prototypes / unregistered controllers.
_SRC_UID = bytes([0x7F, 0xF0]) + os.urandom(4)
_HEADER = b"Art-Net\x00"


def uid_text(uid: bytes) -> str:
    """"0x02A0:12345678" style, as fixtures print it on their display."""
    return f"{uid[0]:02X}{uid[1]:02X}:{uid[2]:02X}{uid[3]:02X}{uid[4]:02X}{uid[5]:02X}"


def uid_bytes(text: str) -> bytes:
    raw = str(text).replace(":", "").strip()
    if len(raw) != 12:
        raise ValueError(f"not an RDM UID: {text!r}")
    return bytes.fromhex(raw)


# ------------------------------------------------------------ Art-Net layer
def build_tod_request(universes: list[int], net: int = 0) -> bytes:
    """ArtTodRequest for up to 32 universes on one Net (TodFull)."""
    addrs = []
    nets = set()
    for u in universes[:32]:
        sub_uni, n = artnet.port_address(int(u), net)
        addrs.append(sub_uni)
        nets.add(n)
    if len(nets) > 1:
        raise ValueError("one ArtTodRequest covers one Net")
    body = bytearray(_HEADER)
    body += struct.pack("<H", TODREQUEST_OP)
    body += bytes([0, artnet.PROTOCOL_VERSION, 0, 0])      # ProtVer, Filler1-2
    body += bytes(7)                                         # Spare1-7
    body += bytes([nets.pop() if nets else 0, 0x00, len(addrs)])  # Net, Command TodFull, AdCount
    body += bytes(addrs) + bytes(32 - len(addrs))
    return bytes(body)


def parse_tod_data(packet: bytes) -> dict | None:
    """ArtTodData -> {"net", "address", "port_address", "uids": [bytes]}."""
    if len(packet) < 28 or packet[:8] != _HEADER:
        return None
    if struct.unpack_from("<H", packet, 8)[0] != TODDATA_OP:
        return None
    net, address, count = packet[21], packet[23], packet[27]
    uids = [bytes(packet[28 + i * 6: 34 + i * 6]) for i in range(count)
            if 34 + i * 6 <= len(packet)]
    return {"net": net, "address": address, "port_address": (net << 8) | address,
            "total": (packet[24] << 8) | packet[25], "uids": uids}


def build_tod_data(net: int, address: int, uids: list[bytes]) -> bytes:
    """(For tests / a software node.)  ArtTodData for one port."""
    body = bytearray(_HEADER)
    body += struct.pack("<H", TODDATA_OP)
    body += bytes([0, artnet.PROTOCOL_VERSION, RDM_VERSION, 1]) + bytes(6)   # ..., Port, Spare1-6
    body += bytes([1, net & 0x7F, 0x00, address & 0xFF])   # BindIndex, Net, CommandResponse, Address
    body += struct.pack(">H", len(uids)) + bytes([0, len(uids)])
    for u in uids:
        body += u
    return bytes(body)


def build_artrdm(rdm: bytes, port_address: int) -> bytes:
    body = bytearray(_HEADER)
    body += struct.pack("<H", RDM_OP)
    body += bytes([0, artnet.PROTOCOL_VERSION, RDM_VERSION, 0]) + bytes(7)   # ..., Filler2, Spare1-7
    body += bytes([(port_address >> 8) & 0x7F, 0x00, port_address & 0xFF])   # Net, ArProcess, Address
    return bytes(body) + rdm


def parse_artrdm(packet: bytes) -> tuple[int, bytes] | None:
    """ArtRdm -> (port_address, rdm message without start code)."""
    if len(packet) < 24 or packet[:8] != _HEADER:
        return None
    if struct.unpack_from("<H", packet, 8)[0] != RDM_OP:
        return None
    return ((packet[21] & 0x7F) << 8) | packet[23], bytes(packet[24:])


# ---------------------------------------------------------------- RDM layer
def _checksum(data: bytes) -> bytes:
    return struct.pack(">H", (SC_RDM + sum(data)) & 0xFFFF)


def build_rdm(dest: bytes, cc: int, pid: int, data: bytes = b"", tn: int = 0,
              src: bytes = _SRC_UID, response_type: int = 1, sub_device: int = 0) -> bytes:
    """One RDM message, as ArtRdm carries it (no 0xCC start code)."""
    length = 24 + len(data)
    msg = bytes([SUB_START, length]) + dest + src + bytes([tn & 0xFF, response_type & 0xFF, 0]) \
        + struct.pack(">HBHB", sub_device, cc, pid, len(data)) + data
    return msg + _checksum(msg)


def parse_rdm(msg: bytes) -> dict | None:
    """An RDM message (no start code) -> its fields, or None if broken."""
    if len(msg) < 25 or msg[0] != SUB_START:
        return None
    length = msg[1]
    if len(msg) < length - 1 + 2 or length < 24:
        return None
    body, check = msg[:length - 1], msg[length - 1:length + 1]
    if _checksum(body) != check:
        return None
    sub_device, cc, pid, pdl = struct.unpack_from(">HBHB", msg, 17)
    return {"dest": bytes(msg[2:8]), "src": bytes(msg[8:14]), "tn": msg[14],
            "response": msg[15], "sub_device": sub_device, "cc": cc, "pid": pid,
            "data": bytes(msg[23:23 + pdl])}


def parse_device_info(data: bytes) -> dict | None:
    if len(data) < 19:
        return None
    (proto, model_id, category, software, footprint, personality, personalities,
     start, sub_devices, sensors) = struct.unpack(">HHHIHBBHHB", data[:19])
    return {"model_id": model_id, "category": category, "software": software,
            "footprint": footprint, "personality": personality,
            "personalities": personalities,
            "address": None if start == 0xFFFF else start}


def _text(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("ascii", "replace").strip()


# ---------------------------------------------------------------- discovery
def _exchange(probe, host: str, port: int, packet: bytes, want, timeout: float,
              first: bool = False) -> list:
    """Send one packet, collect every datagram `want` accepts until timeout
    (or, with `first`, until the first one)."""
    try:
        probe.socks[0].sendto(packet, (host, port))
    except OSError:
        return []
    got = []
    deadline = time.monotonic() + timeout
    while True:
        item = probe.poll(deadline)
        if item is None:
            if time.monotonic() >= deadline:
                break
            continue
        hit = want(item[0])
        if hit is not None:
            got.append((hit, item[1][0]))
            if first:
                break
    return got


def _get(probe, node: str, port: int, pa: int, uid: bytes, pid: int, timeout: float, tn: list,
         data: bytes = b""):
    tn[0] = (tn[0] + 1) & 0xFF
    t = tn[0]

    def want(pkt):
        r = parse_artrdm(pkt)
        if not r:
            return None
        msg = parse_rdm(r[1])
        if msg and msg["src"] == uid and msg["pid"] == pid and msg["tn"] == t and msg["cc"] == GET_RESPONSE:
            return msg
        return None
    for got, _ip in _exchange(probe, node, port, build_artrdm(build_rdm(uid, GET, pid, data, tn=t), pa),
                              want, timeout, first=True):
        return got if got["response"] == ACK else None
    return None


def discover(universes: list[int], host: str = "255.255.255.255", port: int = artnet.ART_NET_PORT,
             net: int = 0, timeout: float = 2.0, per_request: float = 0.4) -> dict:
    """Every RDM light the node(s) know on `universes`: [{uid, universe,
    manufacturer, model, label, address, footprint, personality,
    personalities, mode, node}].  Never raises: a node without RDM just
    doesn't answer, and `error` / `tried` say what happened."""
    universes = sorted({int(u) for u in universes if int(u) >= 1}) or [1]
    try:
        probe = artnet._Probe(port)
    except OSError as exc:
        return {"devices": [], "error": f"cannot open a UDP socket: {exc}", "tried": host}
    devices: list[dict] = []
    try:
        tods = _exchange(probe, host, port, build_tod_request(universes, net), parse_tod_data, timeout)
        seen = set()
        tn = [0]
        for tod, node_ip in tods:
            pa = tod["port_address"]
            universe = pa - (net << 8) + 1
            for uid in tod["uids"]:
                if (uid, pa) in seen:
                    continue
                seen.add((uid, pa))
                row = {"uid": uid_text(uid), "universe": universe, "node": node_ip}
                info = _get(probe, node_ip, port, pa, uid, PID_DEVICE_INFO, per_request, tn)
                if info:
                    row.update(parse_device_info(info["data"]) or {})
                for key, pid in (("manufacturer", PID_MANUFACTURER_LABEL),
                                 ("model", PID_DEVICE_MODEL_DESCRIPTION),
                                 ("label", PID_DEVICE_LABEL)):
                    got = _get(probe, node_ip, port, pa, uid, pid, per_request, tn)
                    if got:
                        row[key] = _text(got["data"])
                if row.get("personality"):
                    got = _get(probe, node_ip, port, pa, uid, PID_DMX_PERSONALITY_DESCRIPTION,
                               per_request, tn, bytes([row["personality"]]))
                    if got and len(got["data"]) > 3:
                        row["mode"] = _text(got["data"][3:])
                devices.append(row)
    finally:
        probe.close()
    devices.sort(key=lambda d: (d["universe"], d.get("address") or 0, d["uid"]))
    return {"devices": devices, "error": None if devices else
            "no RDM lights answered (the node may not support RDM, or RDM is off on its ports)",
            "tried": host, "universes": universes}


def set_address(uid: str, universe: int, address: int, host: str = "255.255.255.255",
                port: int = artnet.ART_NET_PORT, net: int = 0, timeout: float = 1.0) -> dict:
    """SET DMX_START_ADDRESS on one light."""
    if not 1 <= int(address) <= 512:
        raise ValueError("a DMX address is 1-512")
    u = uid_bytes(uid)
    sub_uni, n = artnet.port_address(int(universe), net)
    pa = (n << 8) | sub_uni
    try:
        probe = artnet._Probe(port)
    except OSError as exc:
        return {"ok": False, "error": f"cannot open a UDP socket: {exc}"}
    try:
        def want(pkt):
            r = parse_artrdm(pkt)
            msg = parse_rdm(r[1]) if r else None
            if msg and msg["src"] == u and msg["pid"] == PID_DMX_START_ADDRESS and msg["cc"] == SET_RESPONSE:
                return msg
            return None
        pkt = build_artrdm(build_rdm(u, SET, PID_DMX_START_ADDRESS, struct.pack(">H", int(address)), tn=7), pa)
        got = _exchange(probe, host, port, pkt, want, timeout, first=True)
    finally:
        probe.close()
    if not got:
        return {"ok": False, "error": "the light didn't answer"}
    ok = got[0][0]["response"] == ACK
    return {"ok": ok, "error": None if ok else "the light refused the address"}
