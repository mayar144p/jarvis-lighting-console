"""A virtual Art-Net node with RDM lights, inside the desk.

For trying the whole output path with no hardware: point the output at it
and it answers like a real node - ArtPoll (Find nodes lists it), ArtDmx
(it keeps every universe it is sent and how often), ArtSync, and RDM
(each patched light answers discovery with its model, address, footprint
and mode, and can be readdressed from the desk, as a real light would -
the patch is not touched, which is the point of the comparison).

Pure stdlib, one daemon thread, loopback by default.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

from app import artnet, rdm

NAME = "Jarvis virtual node"
UID_MAN = 0x7FF1           # prototype / experimental manufacturer range


def light_uid(head_no: int) -> bytes:
    return struct.pack(">HI", UID_MAN, int(head_no))


class VirtualNode:
    def __init__(self, host: str = "127.0.0.1", port: int = artnet.ART_NET_PORT, net: int = 0):
        self.host = host
        self.port = int(port)
        self.net = int(net)
        self.error: str | None = None
        self.started_at: float | None = None
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.frames: dict[int, bytes] = {}          # patch universe -> 512 bytes
        self._stamps: dict[int, list[float]] = {}   # recent arrival times
        self.counts = {"dmx": 0, "sync": 0, "poll": 0, "rdm": 0, "tod": 0}
        self.lights: dict[bytes, dict] = {}         # uid -> light

    # -- the lights -------------------------------------------------------
    def set_lights(self, patch: list[dict]) -> None:
        """One RDM light per patched light (fixture lights only)."""
        lights = {}
        for h in patch:
            uid = light_uid(h["head_no"])
            old = self.lights.get(uid) or {}
            lights[uid] = {
                "uid": uid, "head": h["head_no"], "universe": int(h["universe"]),
                # a readdressed light keeps its new address until the rig
                # is re-sent, like the real thing
                "address": old.get("address", int(h["address"])),
                "footprint": int(h.get("channels") or len(h.get("map") or []) or 1),
                "manufacturer": str(h.get("manufacturer") or "Generic"),
                "model": str(h.get("model") or "Light"),
                "label": str(h.get("name") or ""),
                "mode": str(h.get("mode") or f"{h.get('channels')}ch"),
            }
        with self._lock:
            self.lights = lights

    # -- running ----------------------------------------------------------
    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.settimeout(0.2)
        except OSError as exc:
            self.error = f"can't listen on {self.host}:{self.port}: {exc}"
            return False
        self._sock = sock
        self.error = None
        self.started_at = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="jarvis-vnode", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(1.0)
        self._thread = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self.started_at = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                pkt, peer = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle(pkt, peer)
            except Exception:                        # noqa: BLE001 - a bad packet never stops it
                continue

    def _send(self, data: bytes, peer) -> None:
        try:
            self._sock.sendto(data, peer)
        except OSError:
            pass

    # -- packets ----------------------------------------------------------
    def _universe(self, port_address: int) -> int:
        return port_address - (self.net << 8) + 1

    def _handle(self, pkt: bytes, peer) -> None:
        if len(pkt) < 10 or pkt[:8] != b"Art-Net\0":
            return
        op = struct.unpack_from("<H", pkt, 8)[0]
        if op == artnet.ARTDMX_OP:
            d = artnet.decode_artdmx(pkt)
            u = self._universe(d["port_address"])
            data = bytes(d["data"]).ljust(512, b"\0")[:512]
            now = time.monotonic()
            with self._lock:
                self.frames[u] = data
                st = self._stamps.setdefault(u, [])
                st.append(now)
                if len(st) > 120:
                    del st[:-120]
                self.counts["dmx"] += 1
        elif op == artnet.ARTSYNC_OP:
            self.counts["sync"] += 1
        elif op == artnet.ARTPOLL_OP:
            self.counts["poll"] += 1
            ports = sorted({lt["universe"] - 1 for lt in self.lights.values()} or {0})[:4]
            self._send(artnet.build_artpollreply(ip=self.host, port=self.port, net=self.net,
                                                 name=NAME, ports=tuple(ports)), peer)
        elif op == rdm.TODREQUEST_OP:
            self.counts["tod"] += 1
            net, count = pkt[21], pkt[23]
            for sub_uni in pkt[24:24 + count]:
                pa = (net << 8) | sub_uni
                u = self._universe(pa)
                uids = [uid for uid, lt in self.lights.items() if lt["universe"] == u]
                if uids:
                    self._send(rdm.build_tod_data(net, sub_uni, uids), peer)
        elif op == rdm.RDM_OP:
            got = rdm.parse_artrdm(pkt)
            msg = rdm.parse_rdm(got[1]) if got else None
            if not msg:
                return
            light = self.lights.get(msg["dest"])
            if not light:
                return
            self.counts["rdm"] += 1
            reply = self._rdm_reply(light, msg)
            if reply is not None:
                self._send(rdm.build_artrdm(reply, got[0]), peer)

    def _rdm_reply(self, light: dict, msg: dict) -> bytes | None:
        pid = msg["pid"]
        if msg["cc"] == rdm.SET and pid == rdm.PID_DMX_START_ADDRESS and len(msg["data"]) >= 2:
            addr = struct.unpack(">H", msg["data"][:2])[0]
            if 1 <= addr <= 512:
                light["address"] = addr
            return rdm.build_rdm(msg["src"], rdm.SET_RESPONSE, pid, b"", tn=msg["tn"],
                                 src=light["uid"], response_type=rdm.ACK)
        data = {
            rdm.PID_DEVICE_INFO: struct.pack(">HHHIHBBHHB", 0x0100, 1, 0x0101, 1, light["footprint"],
                                             1, 1, light["address"], 0, 0),
            rdm.PID_MANUFACTURER_LABEL: light["manufacturer"].encode()[:32],
            rdm.PID_DEVICE_MODEL_DESCRIPTION: light["model"].encode()[:32],
            rdm.PID_DEVICE_LABEL: light["label"].encode()[:32],
            rdm.PID_DMX_PERSONALITY_DESCRIPTION: bytes([1]) + struct.pack(">H", light["footprint"])
            + light["mode"].encode()[:32],
        }.get(pid)
        if data is None:
            return None
        return rdm.build_rdm(msg["src"], rdm.GET_RESPONSE, pid, data, tn=msg["tn"],
                             src=light["uid"], response_type=rdm.ACK)

    # -- what it has seen ---------------------------------------------------
    def status(self, universe: int | None = None) -> dict:
        now = time.monotonic()
        with self._lock:
            unis = []
            for u in sorted(self.frames):
                st = [t for t in self._stamps.get(u, []) if now - t < 2.0]
                # frames per second over the frames actually seen (a stream
                # that just started is not "1 fps")
                fps = (len(st) - 1) / (st[-1] - st[0]) if len(st) > 2 and st[-1] > st[0] else 0.0
                unis.append({"universe": u, "fps": round(fps, 1),
                             "age_ms": round((now - self._stamps[u][-1]) * 1000) if self._stamps.get(u) else None,
                             "used": sum(1 for b in self.frames[u] if b)})
            out = {"running": self.running, "host": self.host, "port": self.port, "error": self.error,
                   "counts": dict(self.counts), "universes": unis,
                   "lights": [{"uid": rdm.uid_text(lt["uid"]), "head": lt["head"], "universe": lt["universe"],
                               "address": lt["address"], "model": lt["model"]}
                              for lt in sorted(self.lights.values(), key=lambda x: x["head"])]}
            if universe is not None and int(universe) in self.frames:
                out["frame"] = list(self.frames[int(universe)])
        return out
