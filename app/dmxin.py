"""DMX input: receive Art-Net ArtDmx and sACN (E1.31) data packets.

Both protocols decode into the SAME internal representation - one
512-slot buffer per 1-based universe - so the application consumes a
single clean shape no matter what put the frames on the wire:

    state.ingest(universe, data, source)     # from the listener threads
    state.get(universe, channel) -> 0..255   # 1-based channel, non-blocking
    state.channels(universe)    -> bytes     # whole frame copy
    state.snapshot()            -> dict      # observable for UI/debug

Threading model: the listener owns its UDP sockets and two small
daemon threads (one per protocol); recvfrom runs with a short timeout
and everything it touches is the state's own lock - the engine lock and
the HTTP/control threads are never involved, so input can never block
control (requirement: non-blocking input).

Loss of input is graceful: each universe stores its last-seen time and
snapshot() reports `stale` once nothing arrived for DMX_INPUT_TIMEOUT
seconds.  Malformed packets are counted, never raised.

Universe mapping (config DMX_INPUT_MAP, e.g. "1=3,2=3") remaps wire
universes on ingest; channel mapping is explicit on read via
get(universe, channel).  Universes are validated against the E1.31
space (1..63999) because that is what either protocol can address.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

from app.artnet import ARTDMX_OP, decode_artdmx, \
    universe_from_port_address
from app.sacn import ACN_IDENTIFIER, decode_sacn

UNIVERSE_MIN, UNIVERSE_MAX = 1, 63999     # union of Art-Net + E1.31 space
CHANNEL_MIN, CHANNEL_MAX = 1, 512
DEFAULT_TIMEOUT = 2.0                      # seconds until a universe is stale


def parse_map(text) -> dict[int, int]:
    """Parse DMX_INPUT_MAP: "1=3,2=3" -> {1: 3, 2: 3}.

    Each entry is source universe = destination universe.  Empty text is
    the identity mapping; anything malformed raises ValueError so bad
    configuration is reported instead of silently half-applied.
    """
    mapping: dict[int, int] = {}
    text = str(text or "").strip()
    if not text:
        return mapping
    for chunk in text.split(","):
        entry = chunk.strip()
        if not entry:
            continue
        left, sep, right = entry.partition("=")
        if not sep:
            raise ValueError(f"bad DMX input map entry {entry!r} - "
                             f"expected source=destination")
        try:
            src, dst = int(left.strip()), int(right.strip())
        except ValueError:
            raise ValueError(f"bad DMX input map entry {entry!r} - "
                             f"universes must be numbers") from None
        for value, label in ((src, "source"), (dst, "destination")):
            if not UNIVERSE_MIN <= value <= UNIVERSE_MAX:
                raise ValueError(
                    f"bad DMX input map entry {entry!r} - {label} universe "
                    f"must be {UNIVERSE_MIN}..{UNIVERSE_MAX}")
        mapping[src] = dst
    return mapping


def _check_universe(universe) -> int:
    try:
        u = int(universe)
    except (TypeError, ValueError):
        raise ValueError(f"bad universe: {universe!r}") from None
    if not UNIVERSE_MIN <= u <= UNIVERSE_MAX:
        raise ValueError(f"universe must be {UNIVERSE_MIN}..{UNIVERSE_MAX} "
                         f"(got {u})")
    return u


def _check_channel(channel) -> int:
    try:
        c = int(channel)
    except (TypeError, ValueError):
        raise ValueError(f"bad channel: {channel!r}") from None
    if not CHANNEL_MIN <= c <= CHANNEL_MAX:
        raise ValueError(f"channel must be {CHANNEL_MIN}..{CHANNEL_MAX} "
                         f"(got {c})")
    return c


class DmxInputState:
    """Observable store for incoming DMX frames (one buffer per universe).

    Lock is private and only ever held for quick copies - a reader in
    the UI thread and two writer threads can run concurrently without
    touching anything else.
    """

    def __init__(self, mapping: dict[int, int] | None = None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.timeout = float(timeout)
        self.mapping = dict(mapping or {})
        self._lock = threading.Lock()
        self._frames: dict[int, dict] = {}
        self.packets = 0
        self.malformed = 0
        self.ignored = 0

    # -- writing (listener threads) --------------------------------------
    def ingest(self, universe, data, source: str = "artnet",
               sequence=None, priority=None, peer: str = "",
               now: float | None = None) -> int:
        """Store one frame; returns the (mapped) universe it landed in.

        Raises ValueError for an invalid universe or payload - callers
        on the receive path catch it and count a malformed packet.
        """
        u = _check_universe(universe)
        if len(data) < 1 or len(data) > CHANNEL_MAX:
            raise ValueError(f"DMX frame must carry 1..{CHANNEL_MAX} slots "
                             f"(got {len(data)})")
        u = self.mapping.get(u, u)
        slot = bytearray(data)                 # copy: sender may reuse
        now = time.monotonic() if now is None else now
        with self._lock:
            row = self._frames.get(u)
            if row is None:
                row = self._frames[u] = {"data": bytearray(CHANNEL_MAX),
                                         "updated": now, "packets": 0,
                                         "source": source, "sequence": None,
                                         "priority": None, "peer": "",
                                         "terminated": False}
            row["data"][:len(slot)] = slot
            row["updated"] = now
            row["packets"] += 1
            row["source"] = source
            row["sequence"] = sequence
            row["priority"] = priority
            row["peer"] = peer
            row["terminated"] = False
            self.packets += 1
        return u

    def mark_terminated(self, universe) -> None:
        """sACN Stream Terminated: the sender is gone - drop freshness."""
        u = _check_universe(universe)
        u = self.mapping.get(u, u)
        with self._lock:
            row = self._frames.get(u)
            if row is not None:
                row["terminated"] = True
                row["updated"] = 0.0           # age() now reports huge

    # -- reading (app / UI / tests) --------------------------------------
    def universes(self) -> list[int]:
        with self._lock:
            return sorted(self._frames)

    def get(self, universe, channel) -> int:
        """One 1-based channel of one universe (0 when never received)."""
        u, c = _check_universe(universe), _check_channel(channel)
        with self._lock:
            row = self._frames.get(u)
            if row is None:
                return 0
            return row["data"][c - 1]

    def channels(self, universe) -> bytes:
        """Full 512-slot copy of the last frame for one universe."""
        u = _check_universe(universe)
        with self._lock:
            row = self._frames.get(u)
            return bytes(row["data"]) if row else b""

    def age(self, universe, now: float | None = None) -> float | None:
        """Seconds since the last packet (None when never received)."""
        u = _check_universe(universe)
        now = time.monotonic() if now is None else now
        with self._lock:
            row = self._frames.get(u)
            if row is None:
                return None
            return max(0.0, now - row["updated"]) if row["updated"] else None

    def stale(self, universe, now: float | None = None) -> bool:
        """True when the universe went quiet longer than `timeout`."""
        age = self.age(universe, now)
        if age is None:
            return True
        if age == float("inf"):
            return True
        return age > self.timeout

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()

    def snapshot(self, now: float | None = None) -> dict:
        """Observable state for /api/console/input and tests."""
        now = time.monotonic() if now is None else now
        with self._lock:
            rows = []
            for u in sorted(self._frames):
                row = self._frames[u]
                age = (max(0.0, now - row["updated"])
                       if row["updated"] else float("inf"))
                rows.append({
                    "universe": u,
                    "channels": CHANNEL_MAX,
                    "source": row["source"],
                    "sequence": row["sequence"],
                    "priority": row["priority"],
                    "packets": row["packets"],
                    "peer": row["peer"],
                    "age_ms": (int(age * 1000)
                               if age != float("inf") else None),
                    "stale": age > self.timeout,
                    "terminated": row["terminated"],
                })
            stats = {"packets": self.packets, "malformed": self.malformed,
                     "ignored": self.ignored}
        return {"timeout": self.timeout,
                "mapping": {str(k): v for k, v in self.mapping.items()},
                "universes": rows, "stats": stats}


class DmxInputListener:
    """Sniffs ArtDmx + sACN DATA packets on dedicated UDP sockets.

    start() binds synchronously so failures are reported immediately;
    each socket then runs its own daemon thread with a short recv
    timeout.  stop() closes sockets and joins every thread - after it
    returns no listener thread is left behind.
    """

    def __init__(self, state: DmxInputState, artnet_port: int = 6454,
                 sacn_port: int = 5568, net: int = 0,
                 transports: tuple[str, ...] = ("artnet", "sacn")):
        self.state = state
        # Validate here, not at bind time: a bad port in the environment
        # must surface as a clean ValueError the boot path can report,
        # never as a stray OverflowError from socket.bind().
        for label, value in (("artnet", artnet_port), ("sacn", sacn_port)):
            if not 1 <= int(value) <= 65535:
                raise ValueError(f"{label} input port must be 1..65535 "
                                 f"(got {value!r})")
        if not 0 <= int(net) <= 127:
            raise ValueError(f"DMX_NET must be 0..127 (got {net!r})")
        self.artnet_port = int(artnet_port)
        self.sacn_port = int(sacn_port)
        self.net = int(net)
        self.transports = tuple(transports)
        self._stop = threading.Event()
        self._socks: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self.errors: dict[str, str] = {}

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def start(self) -> bool:
        if self.running:
            return True
        self._stop.clear()
        self.errors.clear()
        wanted = []
        if "artnet" in self.transports:
            wanted.append(("artnet", self.artnet_port))
        if "sacn" in self.transports:
            wanted.append(("sacn", self.sacn_port))
        for kind, port in wanted:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("", port))
                sock.settimeout(0.25)
            except OSError as exc:
                sock.close()
                self.errors[kind] = f"cannot bind udp/{port}: {exc}"
                continue
            self._socks.append(sock)
            thread = threading.Thread(target=self._listen,
                                      args=(sock, kind),
                                      name=f"jarvis-dmxin-{kind}",
                                      daemon=True)
            self._threads.append(thread)
            thread.start()
        if not self._threads:
            self.errors.setdefault("error", "no DMX input socket could bind")
        return self.running

    def stop(self) -> None:
        self._stop.set()
        for sock in self._socks:
            try:
                sock.close()
            except OSError:
                pass
        self._socks = []
        for thread in self._threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=2.0)
        self._threads = []

    # -- receive path -----------------------------------------------------
    def _listen(self, sock: socket.socket, kind: str) -> None:
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except (TimeoutError, socket.timeout):
                continue
            except ConnectionResetError:
                # Windows: ICMP unreachable after a rejected send -
                # keep listening.
                continue
            except OSError:
                break                              # socket closed by stop()
            try:
                self._handle(data, f"{addr[0]}:{addr[1]}", kind)
            except Exception:                      # noqa: BLE001
                self.state.malformed += 1

    def _handle(self, data: bytes, peer: str, kind: str) -> None:
        if kind == "artnet":
            self._handle_artnet(data, peer)
        else:
            self._handle_sacn(data, peer)

    def _handle_artnet(self, data: bytes, peer: str) -> None:
        if len(data) < 8 or data[0:8] != b"Art-Net\0":
            self.state.ignored += 1               # not ours: silently skip
            return
        opcode = struct.unpack_from("<H", data, 8)[0]
        if opcode != ARTDMX_OP:
            self.state.ignored += 1               # polls/replies/other
            return
        try:
            frame = decode_artdmx(data)
        except ValueError:
            self.state.malformed += 1
            return
        if frame["length"] > 512 or len(frame["data"]) != frame["length"]:
            self.state.malformed += 1             # truncated / over-long
            return
        if frame["length"] == 0:
            self.state.ignored += 1
            return
        universe = universe_from_port_address(frame["port_address"], self.net)
        self.state.ingest(universe, frame["data"], "artnet",
                          sequence=frame["sequence"], peer=peer)

    def _handle_sacn(self, data: bytes, peer: str) -> None:
        if len(data) < 16 or data[4:16] != ACN_IDENTIFIER:
            self.state.ignored += 1               # not an E1.31 packet
            return
        try:
            packet = decode_sacn(data)
        except ValueError:
            self.state.malformed += 1
            return
        if packet["stream_terminated"]:
            self.state.mark_terminated(packet["universe"])
            return
        self.state.ingest(packet["universe"], packet["data"], "sacn",
                          sequence=packet["sequence"],
                          priority=packet["priority"], peer=peer)


# -- module singleton (wired by main.py) ---------------------------------
STATE: DmxInputState | None = None
LISTENER: DmxInputListener | None = None
_START_ERROR: str | None = None


def start_from_config(config) -> dict:
    """Create + start the input listener from config (boot path).

    Never raises: a bad mapping, an invalid port or a busy socket
    degrades into an error inside the returned status, because losing
    DMX input must not stop the console from starting.
    """
    global STATE, LISTENER, _START_ERROR
    stop()
    _START_ERROR = None
    map_error = None
    mapping: dict[int, int] = {}
    try:
        mapping = parse_map(config.DMX_INPUT_MAP)
    except ValueError as exc:
        map_error = str(exc)
    STATE = DmxInputState(mapping=mapping,
                          timeout=float(config.DMX_INPUT_TIMEOUT))
    try:
        LISTENER = DmxInputListener(STATE,
                                    artnet_port=config.DMX_INPUT_ARTNET_PORT,
                                    sacn_port=config.DMX_INPUT_SACN_PORT,
                                    net=config.DMX_NET)
        LISTENER.start()
    except (ValueError, OSError) as exc:
        LISTENER = None
        _START_ERROR = str(exc)
    status = snapshot()
    if map_error:
        status["map_error"] = map_error
    return status


def stop() -> None:
    global STATE, LISTENER, _START_ERROR
    if LISTENER is not None:
        LISTENER.stop()
        LISTENER = None
    STATE = None
    _START_ERROR = None


def snapshot() -> dict:
    """API shape for /api/console/input and /api/status."""
    if STATE is None:
        return {"enabled": False, "running": False, "universes": [],
                "stats": {"packets": 0, "malformed": 0, "ignored": 0},
                "errors": {}}
    data = STATE.snapshot()
    if LISTENER is None:
        # Enabled by config but the sockets could not start - observable
        # instead of silently "off", so the operator sees WHY.
        data.update({"enabled": True, "running": False,
                     "errors": {}, "error": _START_ERROR})
        return data
    data.update({"enabled": True, "running": LISTENER.running,
                 "errors": dict(LISTENER.errors),
                 "artnet_port": LISTENER.artnet_port,
                 "sacn_port": LISTENER.sacn_port})
    if _START_ERROR:
        data["error"] = _START_ERROR
    return data
