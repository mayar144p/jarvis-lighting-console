"""Output: live / blind, the DMX target, the output thread.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import json
import threading
import time

from app import config, netif
from app.artnet import ArtNetSender
from app.engine_base import (
    DMX_TARGET_MODES,
    _clamp,
    _truthy,
    clean_dmx_target,
    pick_auto_broadcast,
)
from app.engine_support import SLOTS
from app.sacn import SacnSender
from app.virtualnode import VirtualNode


class OutputMixin:
    # --- output / safety ---------------------------------------------------
    def _a_status(self, **_):
        return {"mode": self.mode, "dry_run": self.dry_run,
                "live": self.live, "patched": len(self.patch),
                "selected": len(self.selected), "blackout": self.blackout,
                "master": self.master, "output": self._output_public(),
                # The lock, so the agent and the API can see it without
                # guessing: an agent that tries to record a cue into a
                # locked desk should say so rather than discovering it by
                # the refusal.
                "lock": self.lock_state,
                "lock_has_password": bool(getattr(self, "_lock_hash", "")),
                "following": [pb["n"] for pb in self.playbacks
                              if pb["follow"]["on"]],
                "active_playbacks": [pb["n"] for pb in self.playbacks
                                     if pb["active"]]}

    def _a_set_output(self, state=None, confirm=False, **_):
        if state is None:
            raise ValueError("state is required")
        want = state in (True, 1, "1", "true", "on", "start")
        if want:
            if not self.dry_run and not confirm:
                raise ValueError("real DMX output needs confirm:true")
            self.live = True
            self._start_output()
            return {"live": True, "dry_run": self.dry_run,
                    "summary": "output started" +
                               (" (dry run)" if self.dry_run else " - LIVE")}
        self.live = False
        self._stop_output()
        return {"live": False, "summary": "output stopped"}

    def _a_set_dry_run(self, state=None, confirm=False, **_):
        """Turn dry run on and off WHILE THE DESK IS RUNNING.

        Until this existed the only way out of dry run was to edit .env
        and restart - which is exactly the thing that stops someone
        testing on a real rig, and the reason `dry_run` was still true the
        first time somebody plugged a node in.

        The sender is built ONCE with `dry_run` baked in, so this reaches
        into it rather than rebuilding: replacing the sender mid-show
        would drop the first frame and re-open a socket, and the operator
        would see the rig blink for a reason nobody could name.

        Turning dry run OFF while the output is LIVE needs `confirm`,
        because that is the moment the desk starts driving real fixtures.
        Turning it ON is always allowed - that is the safe direction, and
        it is the one you want in a hurry.
        """
        want = _truthy(state) if state is not None else not self.dry_run
        if not want and self.live and not confirm:
            raise ValueError(
                "turning dry run off while the output is LIVE starts "
                "driving real fixtures — pass confirm:true to do it")
        was = self.dry_run
        self.dry_run = bool(want)
        # The sender holds its own copy, and it is the thing that decides
        # whether a socket is touched at all.
        if self._sender is not None:
            self._sender.dry_run = self.dry_run
        self._log("dry_run", True, None,
                  "dry run on" if self.dry_run else "dry run OFF")
        self._autosave()
        return {"dry_run": self.dry_run, "live": self.live,
                "was": was,
                "summary": ("DRY RUN — frames are built and counted, "
                            "nothing leaves this machine"
                            if self.dry_run else
                            "LIVE — frames are going to the network"
                            + ("" if self.live else
                               " (press GO LIVE to start sending)"))}

    def _a_blackout(self, state=1, **_):
        if isinstance(state, str):
            self.blackout = state.strip().lower() not in (
                "0", "off", "false", "no", "release")
        else:
            self.blackout = bool(int(state or 0))
        if self.blackout:
            self._a_fx_kill()          # blackout stops every effect and disarms
        return {"blackout": self.blackout,
                "summary": "blackout on" if self.blackout else
                           "blackout released"}

    def _a_master(self, level=None, **_):
        if level is None:
            raise ValueError("level is required (0-100)")
        self.master = _clamp(level, 0, 100)
        return {"master": self.master}

    def _start_output(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        thread = threading.Thread(target=self._run, name="jarvis-dmx",
                                  daemon=True)
        self._thread = thread
        thread.start()

    def _stop_output(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and \
                thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        self.output["running"] = False

    # -- the virtual node: the whole output path with no hardware ----------
    def _a_virtual_node(self, state=None, **_):
        """A virtual Art-Net node inside the desk (loopback), with an RDM
        light for each patched light.  On: the output goes to it; off:
        back to where it went before."""
        node = self.__dict__.get("vnode")
        want = (not (node and node.running)) if state is None else \
            str(state).lower() in ("1", "true", "on", "yes", "start")
        if want:
            prev = self.__dict__.get("_vnode_prev")
            self._vnode_prev = prev if prev is not None else dict(self.dmx_target)
            self._a_set_dmx_target(mode="node", host="127.0.0.1", transport="artnet")
            _t, host, port = self._dmx_resolved()
            if node is None or node.port != port:
                if node is not None:
                    node.stop()
                node = self.vnode = VirtualNode(host, port, config.DMX_NET)
            self._vnode_sync(force=True)
            if not node.start():
                self.dmx_target = clean_dmx_target(self._vnode_prev)
                self._vnode_prev = None
                raise ValueError(node.error)
            return {"virtual_node": True, "host": f"{host}:{port}",
                    "summary": f"virtual node on at {host}:{port}: the output goes to it"}
        if node is not None:
            node.stop()
        prev = self.__dict__.get("_vnode_prev")
        if prev is not None:
            self.dmx_target = clean_dmx_target(prev)
            self._vnode_prev = None
        return {"virtual_node": False, "summary": "virtual node off"}

    def _vnode_sync(self, force: bool = False) -> None:
        node = self.__dict__.get("vnode")
        if node is not None and (force or self.__dict__.get("_vnode_rev") != self.patch_rev):
            node.set_lights([h for h in self.patch if self._head_class(h) == "light"] or self.patch)
            self._vnode_rev = self.patch_rev

    def vnode_status(self, universe: int | None = None) -> dict:
        """What the virtual node has been sent (and one universe's bytes)."""
        with self.lock:
            node = self.__dict__.get("vnode")
            if node is None:
                return {"running": False}
            self._vnode_sync()
        return node.status(universe)

    def shutdown(self) -> None:
        """Process exit: stop the threads, optionally send blackout."""
        self.live = False
        if self.__dict__.get("vnode") is not None:
            self.vnode.stop()
        self._prodj_stop()
        self._link_stop()
        self._ap_shutdown()
        op = self.__dict__.get("ai_operator")
        if op:
            op["on"] = False                # the AI operator's loop ends
        ev = self.__dict__.get("_op_stop_ev")
        if ev:
            ev.set()
        self._osc_stop()
        try:
            self._autosave(force=True)     # never lose the last edit
        except Exception:
            pass
        self._stop_output()
        self._stop_follow_thread()
        self.tl["playing"] = False
        self._tl_stop.set()
        self._stop_writer()               # flush any queued autosave
        if config.DMX_BLACKOUT_ON_EXIT and not self.dry_run:
            try:
                sender = self._get_sender()
                black = {u: bytearray(SLOTS) for u in self._universes()}
                for universe, buf in black.items():
                    for _ in range(3):
                        sender.send(universe, buf)
            except Exception:
                pass
        if self._sender is not None:
            self._sender.close()

    def _get_sender(self) -> ArtNetSender | SacnSender:
        """Lazy sender for the configured transport (Art-Net or sACN).

        Both classes expose the same interface (send/stats/close/dry_run
        and the frame counters), so patch, merge, FX and frame logic
        never change with the wire protocol - only _dispatch's socket
        target does.
        """
        if self._sender is not None and self._sender_fixed:
            return self._sender
        transport, host, port = self._dmx_resolved()
        cur = self._sender
        if cur is not None and (getattr(cur, "transport", "artnet"), cur.host,
                                cur.port) == (transport, host, port):
            return cur
        # The target changed (Settings, a loaded show, a cable plugged in
        # under auto): swap senders between two frames, no restart.
        if transport == "sacn":
            new = SacnSender(host, port, config.DMX_NET, self.dry_run,
                             priority=config.SACN_PRIORITY,
                             source_name=config.SACN_SOURCE_NAME,
                             cid=config.SACN_CID or None,
                             sync_universe=config.SACN_SYNC_UNIVERSE)
        else:
            new = ArtNetSender(host, port, config.DMX_NET, self.dry_run)
        self._sender = new
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        return new

    # -- where the DMX goes ----------------------------------------------
    AUTO_HOST_TTL = 10.0

    def _dmx_resolved(self) -> tuple[str, str, int]:
        """(transport, host, port) the output should use right now.

        Cheap enough for the output thread: the only slow part, asking the
        OS for its adapters, runs on a helper thread and is cached.
        """
        t = self.dmx_target
        transport = t.get("transport") or config.DMX_TRANSPORT
        if transport == config.DMX_TRANSPORT:
            port = config.DMX_PORT
        else:
            port = 5568 if transport == "sacn" else 6454
        mode = t.get("mode") or "auto"
        if mode in ("node", "broadcast") and t.get("host"):
            return transport, t["host"], port
        if transport == "sacn":
            if transport == config.DMX_TRANSPORT and not config.DMX_HOST_IS_DEFAULT:
                return transport, config.DMX_HOST, port
            return transport, "multicast", port
        if (mode == "auto" and config.DMX_TRANSPORT == "artnet"
                and not config.DMX_HOST_IS_DEFAULT):
            return transport, config.DMX_HOST, port     # .env wins in auto
        return transport, self._auto_broadcast(), port

    def _auto_broadcast(self) -> str:
        now = time.monotonic()
        if now - self._auto_host_at > self.AUTO_HOST_TTL and not self._auto_host_busy:
            self._auto_host_busy = True
            threading.Thread(target=self._refresh_auto_host, daemon=True,
                             name="jarvis-netif").start()
        if self._auto_host:
            return self._auto_host
        host = config.DMX_HOST if config.DMX_TRANSPORT == "artnet" else ""
        return host if host and host != "multicast" else "255.255.255.255"

    def _refresh_auto_host(self, ifaces: list[dict] | None = None) -> None:
        try:
            if ifaces is None:
                ifaces = netif.interfaces()
            self._auto_host = pick_auto_broadcast(ifaces)
        except Exception:
            pass
        finally:
            self._auto_host_at = time.monotonic()
            self._auto_host_busy = False

    def _a_set_dmx_target(self, mode=None, host=None, transport=None, **_):
        """Where the DMX goes at this venue.  mode: auto (the adapters'
        broadcast), node (one node's IP, unicast) or broadcast (a
        broadcast address you give).  Saved with the show."""
        import ipaddress
        cur = dict(self.dmx_target)
        if mode is not None:
            cur["mode"] = str(mode).lower()
        if host is not None:
            cur["host"] = str(host).strip()
        if transport is not None:
            cur["transport"] = str(transport).lower()
        if cur["mode"] not in DMX_TARGET_MODES:
            raise ValueError("mode is auto, node or broadcast")
        if cur["transport"] not in ("", "artnet", "sacn"):
            raise ValueError("transport is artnet or sacn")
        if cur["mode"] != "auto":
            h = cur["host"]
            if not h:
                raise ValueError("give the node's IP address, e.g. 2.0.0.10")
            if not (h == "multicast" and cur["transport"] == "sacn"):
                try:
                    ip = ipaddress.IPv4Address(h)
                except ValueError:
                    raise ValueError(f"{h!r} is not an IP address like 2.0.0.10")
                if ip.is_loopback and cur["mode"] == "broadcast":
                    raise ValueError("a broadcast address cannot be loopback")
                if ip.is_multicast or ip.is_unspecified:
                    raise ValueError(f"{h} cannot receive DMX")
        self.dmx_target = clean_dmx_target(cur)
        transport, host_, port = self._dmx_resolved()
        what = {"auto": "auto", "node": "node", "broadcast": "broadcast"}[
            self.dmx_target["mode"]]
        return {"target": dict(self.dmx_target),
                "resolved": {"transport": transport, "host": host_, "port": port},
                "summary": f"DMX output: {what} -> {host_}:{port} ({transport})"}

    def network_info(self) -> dict:
        """The adapters, the output target and whether it can be reached
        (the Settings -> Output page).  Runs the OS query: not for the
        output thread."""
        ifaces = netif.interfaces()
        self._refresh_auto_host(ifaces)
        with self.lock:
            target = dict(self.dmx_target)
            transport, host, port = self._dmx_resolved()
        verdict = None
        if host and host not in ("multicast", "255.255.255.255"):
            if target["mode"] == "node" or not host.endswith(".255"):
                verdict = netif.check(host, ifaces)
            else:
                verdict = {"ok": any(netif.broadcast_for(i["ip"], i.get("mask")) == host
                                     for i in ifaces), "via": None, "suggest": None}
                verdict["message"] = ("an adapter is on that network" if verdict["ok"] else
                                      f"no adapter's broadcast is {host}")
        return {"interfaces": [dict(i, broadcast=netif.broadcast_for(i["ip"], i.get("mask")))
                               for i in ifaces],
                "target": target,
                "resolved": {"transport": transport, "host": host, "port": port},
                "env_host": None if config.DMX_HOST_IS_DEFAULT else config.DMX_HOST,
                "check": verdict}

    def _run(self) -> None:
        """Output thread: one frame per tick, deadline scheduled."""
        period = 1.0 / float(config.DMX_HZ)
        period_ema_ms = period * 1000.0
        target = time.monotonic()
        previous = None
        self.output["running"] = True
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now < target:
                    self._stop.wait(min(0.004, target - now))
                    continue
                try:
                    with self.lock:
                        frames = self.build_frames()
                    self._dispatch(frames)
                    # the screens' 3D look, right after the frame is out: the
                    # next tick is a whole period away, so screens never make
                    # the DMX wait for the lock (only while someone watches)
                    if time.monotonic() - self._look_wanted < 1.0:
                        self._look_cache = (time.monotonic(),
                                            json.dumps(self.look_rows(), separators=(",", ":")))
                except Exception as exc:            # never die silently
                    self.output["errors"] += 1
                    self.output["last_error"] = str(exc)
                    self.output["last_error_at"] = time.monotonic()
                done = time.monotonic()
                if previous is not None:
                    inst_ms = (done - previous) * 1000
                    self.output["last_tick_age_ms"] = round(inst_ms, 1)
                    self._gaps.append(inst_ms)
                    # smooth the PERIOD, then invert: averaging rates biases
                    # high whenever jitter makes some ticks short
                    period_ema_ms = period_ema_ms * 0.85 + inst_ms * 0.15
                    self.output["hz"] = round(1000.0 / period_ema_ms, 1)
                self.output["drift_ms"] = round(
                    max(0.0, done - target) * 1000, 1)
                previous = done
                target += period
                if target < done - period:
                    target = done           # fell behind: resync, no burst
        finally:
            self.output["running"] = False

    def _dispatch(self, frames: dict) -> None:
        sender = self._get_sender()
        for universe, buf in frames.items():
            if sender.dry_run:
                self.output["simulated_frames"] += 1
                continue
            if sender.send(universe, buf):
                self.output["frames_sent"] += 1
            else:
                self.output["errors"] += 1
                self.output["last_error"] = sender.last_error
                self.output["last_error_at"] = time.monotonic()
        if frames and config.DMX_SYNC and not sender.dry_run:
            sync = getattr(sender, "sync", None)
            if sync is not None:
                sync(len(frames))
