"""This computer's IPv4 networks, and whether a lighting node is on one.

Every venue's lighting network is different - a Chauvet or ENTTEC node on
2.0.0.10/255.255.0.0 in one club, 192.168.1.50/24 in the next - and the
most common "nothing happens" at load-in is that the laptop simply has no
address on the node's network (the Ethernet port is still on DHCP, or the
Wi-Fi is the only network it is on).  This module answers that question
precisely instead of guessing: it lists every IPv4 address with its mask,
and for a node address says which of them can reach it, or what to set.

Standard library only: the OS's own tool is asked (ipconfig, ifconfig,
ip), with a socket-based fallback that at least finds the addresses.
"""
from __future__ import annotations

import ipaddress
import platform
import re
import socket

from app import procs


# Listing the adapters must never hold up "Find nodes": on some machines
# (GitHub's Ubuntu runners, a laptop mid-Wi-Fi-change) `ip` / `ifconfig`
# hang until killed, and a scan runs up to two of them.
RUN_TIMEOUT_S = 1.5


def _run(cmd: list[str]) -> str:
    return procs.run(cmd, RUN_TIMEOUT_S).stdout


def parse_ipconfig(text: str) -> list[dict]:
    """Windows `ipconfig` output -> [{name, ip, mask}]."""
    out, name, ip = [], "", None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line and not line.startswith(" ") and line.endswith(":"):
            name, ip = line[:-1].strip(), None
            continue
        m = re.search(r"IPv4[^:]*:\s*([\d.]+)", line)
        if m:
            ip = m.group(1)
            continue
        m = re.search(r"Subnet Mask[^:]*:\s*([\d.]+)", line)
        if m and ip:
            out.append({"name": name, "ip": ip, "mask": m.group(1)})
            ip = None
    return out


def parse_ifconfig(text: str) -> list[dict]:
    """macOS/BSD `ifconfig` output -> [{name, ip, mask}]."""
    out, name = [], ""
    for line in text.splitlines():
        m = re.match(r"^(\S+?):? ", line)
        if m and not line.startswith(("\t", " ")):
            name = m.group(1).rstrip(":")
        m = re.search(r"inet (?:addr:)?([\d.]+)\s+(?:netmask|Mask:)\s*(0x[0-9a-fA-F]+|[\d.]+)", line)
        if m:
            mask = m.group(2)
            if mask.startswith("0x"):
                mask = str(ipaddress.IPv4Address(int(mask, 16)))
            out.append({"name": name, "ip": m.group(1), "mask": mask})
    return out


def parse_ip_addr(text: str) -> list[dict]:
    """Linux `ip -o -4 addr` output -> [{name, ip, mask}]."""
    out = []
    for line in text.splitlines():
        m = re.search(r"^\d+:\s+(\S+)\s+inet\s+([\d.]+)/(\d+)", line)
        if m:
            net = ipaddress.IPv4Network(f"0.0.0.0/{m.group(3)}")
            out.append({"name": m.group(1), "ip": m.group(2), "mask": str(net.netmask)})
    return out


def _fallback() -> list[dict]:
    found: list[str] = []
    for probe in ("2.255.255.255", "10.255.255.255", "172.16.0.1", "192.168.0.1",
                  "192.0.2.1", "8.8.8.8"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.2)
            s.connect((probe, 9))               # UDP connect sends nothing
            ip = s.getsockname()[0]
            if ip and ip not in found:
                found.append(ip)
        except OSError:
            pass
        finally:
            s.close()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    return [{"name": "", "ip": ip, "mask": None} for ip in found]


def interfaces() -> list[dict]:
    """Every IPv4 address this computer has, loopback excluded."""
    system = platform.system()
    if system == "Windows":
        rows = parse_ipconfig(_run(["ipconfig"]))
    elif system == "Darwin":
        rows = parse_ifconfig(_run(["ifconfig"]))
    else:
        rows = parse_ip_addr(_run(["ip", "-o", "-4", "addr", "show"])) or \
            parse_ifconfig(_run(["ifconfig"]))
    if not rows:
        rows = _fallback()
    seen, out = set(), []
    for r in rows:
        if r["ip"].startswith(("127.", "169.254.")) or r["ip"] in seen:
            continue
        seen.add(r["ip"])
        out.append(r)
    return out


def _network(ip: str, mask: str | None) -> ipaddress.IPv4Network:
    if not mask:
        mask = "255.0.0.0" if ip.startswith(("2.", "10.")) else "255.255.255.0"
    return ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)


def broadcast_for(ip: str, mask: str | None) -> str:
    return str(_network(ip, mask).broadcast_address)


def check(target: str, ifaces: list[dict] | None = None,
          node_mask: str | None = None) -> dict:
    """Can this computer reach `target`?  {ok, via, message, suggest}."""
    ifaces = interfaces() if ifaces is None else ifaces
    try:
        tgt = ipaddress.IPv4Address(target)
    except ValueError:
        return {"ok": False, "via": None, "suggest": None,
                "message": f"{target!r} is not an IPv4 address"}
    if tgt.is_loopback:
        # 127.x is this computer itself (the virtual node, a node program on
        # this PC): always reachable, whatever the network ports have
        return {"ok": True, "via": {"name": "this computer", "ip": str(tgt)}, "suggest": None,
                "message": "this computer itself (a node program or the virtual node on this PC)"}
    for i in ifaces:
        if tgt in _network(i["ip"], i.get("mask")):
            return {"ok": True, "via": i, "suggest": None,
                    "message": f"reachable from {i['name'] or 'this computer'} ({i['ip']})"}
    # suggest a free-looking address on the node's network
    net = _network(target, node_mask or ("255.255.0.0" if target.startswith("2.") else None))
    parts = target.split(".")
    host = 100 if parts[3] != "100" else 101
    suggest = {"ip": ".".join(parts[:3] + [str(host)]), "mask": str(net.netmask)}
    have = ", ".join(i["ip"] for i in ifaces) or "none"
    return {"ok": False, "via": None, "suggest": suggest,
            "message": (f"this computer has no address on the {net} network the node is on "
                        f"(it has: {have}). Plug into the lighting network and give that "
                        f"network port a fixed address like {suggest['ip']}, mask {suggest['mask']}.")}
