"""The phone and tablet remote (web/remote.html): where a phone finds the desk.

The desk's own screens run on this computer (127.0.0.1); a phone needs the
computer's address on the Wi-Fi, and the pairing code.  ``info()`` gives both
to a screen that is already signed in (it holds the code anyway), so the
desk can draw a QR code a phone scans instead of typing either.
"""

from __future__ import annotations

import ipaddress
import socket

from app import config


def _usable(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.version == 4 and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast)


def lan_addresses() -> list[str]:
    """This computer's IPv4 addresses a phone could reach: the Wi-Fi /
    network one first, a Tailscale one (100.64.0.0/10) after it."""
    found: list[str] = []
    # the address the computer would use to reach the internet - no packet
    # is sent (a UDP "connect" only picks the route)
    for probe in ("192.168.0.1", "10.0.0.1", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.2)
                s.connect((probe, 9))
                found.append(s.getsockname()[0])
        except OSError:
            continue
    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(res[4][0])
    except OSError:
        pass
    out: list[str] = []
    for a in found:
        if _usable(a) and a not in out:
            out.append(a)
    rank = {"lan": 0, "tailscale": 1, "vpn": 2}
    return sorted(out, key=lambda a: rank[kind(a)])


def is_tailscale(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


_LAN = [ipaddress.ip_network(n) for n in ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12")]


def kind(addr: str) -> str:
    """"lan" (a home / venue network: 192.168.x, 10.x, 172.16-31.x - a phone
    on the Wi-Fi reaches it), "tailscale", or "vpn": any other address on
    this computer is a VPN's own (Radmin VPN gives 26.x, Hamachi 25.x) -
    only devices on that VPN reach it, never a phone on the Wi-Fi."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return "vpn"
    if any(ip in n for n in _LAN):
        return "lan"
    return "tailscale" if is_tailscale(addr) else "vpn"


def info() -> dict:
    """Whether phones can reach this desk, at which addresses, with which code."""
    if not config.requires_token():
        # (bound to this computer only: Desk -> Other computers, phones and tablets turns it on)
        return {"network": False, "addresses": [], "port": config.PORT, "code": ""}
    return {"network": True, "port": config.PORT, "code": config.CONSOLE_TOKEN,
            "addresses": [{"address": a, "tailscale": kind(a) == "tailscale", "vpn": kind(a) == "vpn"}
                          for a in lan_addresses()]}
