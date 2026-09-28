"""What address is this thing sending Art-Net from, and to?

Run it when the rig will not answer and you need to know whether the
console is even on the right network.  It answers four questions:

  1. what is this machine's address on the LAN
  2. what is the console actually sending to
  3. can it see anything at all on that network
  4. what a node would see as the source of an ArtPoll from here

Pure stdlib, no packets sent except a real ArtPoll (which is a question,
not a command - a node that answers is behaving normally).
"""
import pathlib
import socket
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

ARTNET_PORT = 6454


def rule(title=""):
    print(("  " + "-" * 62) if title else ("-" * 64))


def main() -> int:
    print("=" * 64)
    print("network")
    print("=" * 64)
    print("  this machine's LAN address : %s"
          % (config.LOCAL_IP or "NOT DETECTED"))
    print("  DMX_HOST (in use)          : %s" % config.DMX_HOST)
    where = ("auto-detected" if config.DMX_HOST_IS_DEFAULT
             else "set in .env")
    print("  that value is              : %s" % where)
    print("  transport                  : %s, port %d"
          % (config.DMX_TRANSPORT, config.DMX_PORT))
    print("  dry run                    : %s"
          % ("yes — nothing leaves this machine" if config.CONSOLE_DRY_RUN
             else "no — frames are being sent"))
    rule()
    if not config.LOCAL_IP:
        print("  No LAN address could be found.  That means no interface has a")
        print("  usable route, which on Windows usually means the network is")
        print("  not connected yet.  Plug in / join the rig's network and")
        print("  start again.")
        return 1
    if config.CONSOLE_DRY_RUN:
        print("  DRY RUN IS ON.  The rig will never light up until you set")
        print("  CONSOLE_DRY_RUN=false and press GO LIVE.  That is the next")
        print("  thing to try if everything below looks right.")
        rule()

    # Every address this machine holds, so a VPN or a second NIC can be
    # spotted - those are the usual reason the detected one is wrong.
    print("  every address on this machine:")
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127."):
                continue
            mark = "  <-- in use" if ip == config.DMX_HOST else ""
            print("    %-16s %s%s" % (ip, "private" if config._is_private(ip)
                                     else "public", mark))
    except OSError as exc:
        print("    (could not enumerate: %s)" % exc)
    rule()

    # Can we see anything?  An ArtPoll is a QUESTION.  A node that answers
    # is behaving correctly, so this is safe to run on a live rig.
    print("  listening for an ArtPoll answer on %s:%d ..."
          % (config.DMX_HOST, ARTNET_PORT))
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        rx.bind(("", ARTNET_PORT))
    except OSError as exc:
        print("    could not listen: %s" % exc)
        print("    (something else already has 6454 — another app, or a")
        print("     previous instance of this one)")
        rx.close()
        return 1
    rx.settimeout(2.0)

    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Art-Net: "Art-Net\0", OpPoll, protocol version 0, talk/priority.
        poll = b"Art-Net\x00" + bytes([0x00, 0x00, 0x00, 0xE0]) + b"\x00" * 7
        tx.sendto(poll, (config.DMX_HOST, ARTNET_PORT))
    except OSError as exc:
        print("    could not send: %s" % exc)
        tx.close()
        rx.close()
        return 1
    print("    sent ArtPoll")
    found = 0
    self_answered = 0
    mine = {config.LOCAL_IP, "127.0.0.1", socket.gethostname()}
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            data, addr = rx.recvfrom(1024)
        except socket.timeout:
            break
        who = addr[0]
        if who in mine:
            # THIS IS US.  The console's own sender is listening on 6454
            # and answers a poll, so without this check the tool reports
            # "the network is fine" on a rig with nothing on it - which is
            # the one answer that must never be given on a false positive.
            self_answered += 1
            print("    answered by %s — that is THIS MACHINE" % who)
            print("      (your own console replying to its own poll; it is")
            print("       not a node, so this is not evidence of anything)")
            continue
        found += 1
        print("    ANSWERED by %s  (%d bytes)" % (who, len(data)))
        if data[:8] == b"Art-Net\x00":
            op = data[8]
            names = {0x2100: "ArtPollReply", 0x2000: "ArtPoll"}
            print("      %s" % names.get(op, "op 0x%04x" % op))
            # A reply's IP field is the node's idea of where to send data.
            if len(data) >= 29:
                ip = ".".join(str(b) for b in data[21:25])
                print("      it wants data sent to %s" % ip)
                print("      -> set DMX_HOST=%s in .env if that is a "
                      "different address" % ip)
    tx.close()
    rx.close()
    rule()
    if not found:
        if self_answered:
            print("  No NODE answered — only this machine did.")
        else:
            print("  Nothing answered at all.")
        print("  In order of likelihood:")
        print("   1. a node with Art-Net (not Art-Net 2) and ArtPoll "
              "switched ON")
        print("   2. the node is on a different SUBNET — check its IP mask")
        print("   3. the cable, or the node's own output, is not enabled")
        print("   4. a firewall is dropping UDP %d" % ARTNET_PORT)
        print("  Set DMX_HOST to a specific node IP if you know one, and run")
        print("  this again — a unicast poll usually finds a node that a")
        print("  broadcast missed.")
        return 2
    print("  %d NODE(S) answered.  The network is fine; the problem is" % found)
    print("  above this line if the lights still do not move.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
