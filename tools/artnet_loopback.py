"""Art-Net loopback test - proves the packet path without any hardware.

    python tools/artnet_loopback.py [--seconds 2] [--port 6454] [--host 127.0.0.1]

Binds a local UDP receiver, drives the Jarvis engine for a couple of
seconds and then verifies everything the Art-Net spec requires:

  * header, OpCode (little-endian 0x5000), protocol version, length
  * per-universe sequence counters increment and wrap 255 -> 1
  * frame rate sits within +-10%% of DMX_HZ
  * decoding the payload reproduces the programmer state

Exit code 0 = all checks passed. No hardware is touched: the engine sends
to 127.0.0.1 (loopback) even with CONSOLE_DRY_RUN off.
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, fixtures                       # noqa: E402
from app import artnet                                 # noqa: E402
from app import engine as engine_mod                   # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}  {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Art-Net loopback test")
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--port", type=int, default=6454)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print(f"Art-Net loopback -> {args.host}:{args.port} "
          f"for {args.seconds}s @ {config.DMX_HZ} Hz")

    # 1. receiver -------------------------------------------------------
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((args.host, args.port))
        sock.settimeout(0.25)
    except OSError as exc:
        print(f"  FAIL cannot bind {args.host}:{args.port}: {exc}")
        print("       (another node/controller is holding the port - "
              "try --port 6456)")
        return 1

    # 2. engine pointed at the receiver ---------------------------------
    # A scratch database, NOT the operator's.  This tool used to seed the
    # four generic profiles into data/fixtures.db, so running a loopback
    # check quietly added entries to the live fixture library - and with
    # seeding now off by default it would also fail, because the library
    # holds only what the GDTF Share brought in.  A diagnostic must not
    # mutate the thing it is diagnosing.
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="jarvis-loopback-"))
    fixtures.seed_generics(scratch / "fixtures.db")
    print(f"       scratch library: {scratch / 'fixtures.db'}")
    sender = artnet.ArtNetSender(args.host, args.port, config.DMX_NET,
                                 dry_run=False)
    eng = engine_mod.Engine(db_path=scratch / "fixtures.db", dry_run=False,
                            sender=sender)
    res = eng.act("add_heads", query="LED PAR", qty=4)
    if not res["ok"]:
        print(f"  FAIL patch: {res['error']}")
        return 1
    eng.act("select_all")
    eng.act("set_intensity", level=70)          # -> DMX 178
    eng.act("set_colour", hex="#ff0000")        # -> 255,0,0
    eng.act("set_output", state=True, confirm=True)

    # 3. collect frames ---------------------------------------------------
    packets: dict[int, list[tuple[float, bytes]]] = {}
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        try:
            data = sock.recv(2048)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            decoded = artnet.decode_artdmx(data)
        except ValueError:
            continue
        packets.setdefault(decoded["port_address"], []).append(
            (time.monotonic(), data))

    eng.act("set_output", state=False)
    eng.shutdown()
    sock.close()

    # 4. verdict ----------------------------------------------------------
    check("frames received", bool(packets),
          "no ArtDmx packets arrived")
    if not packets:
        print(f"\n{PASS} passed, {FAIL} failed")
        return 1

    frames = packets.get(0, [])                 # patch universe 1
    check("universe 1 frames", len(frames) > 0, f"port addresses {list(packets)}")

    sample = frames[0][1] if frames else b""
    check("packet size 530", len(sample) == 530, str(len(sample)))
    check("header id", sample[0:8] == b"Art-Net\0", repr(sample[0:8]))
    check("opcode little-endian", sample[8] == 0x00 and sample[9] == 0x50,
          str(sample[8:10]))
    check("protocol version 14", sample[10] == 0x00 and sample[11] == 0x0E,
          str(sample[10:12]))
    check("length 512 big-endian",
          sample[16] == 0x02 and sample[17] == 0x00, str(sample[16:18]))

    # sequence: increment, wrap 255 -> 1
    seqs = [artnet.decode_artdmx(p)["sequence"] for _t, p in frames]
    wrapped = all(nxt == (prev % 255) + 1
                  for prev, nxt in zip(seqs, seqs[1:]))
    check("sequence increments", wrapped or len(seqs) < 3,
          str(seqs[:8]))
    check("sequence stays in 1..255", all(1 <= s <= 255 for s in seqs),
          str(seqs[:8]))

    # rate within +-10%
    duration = frames[-1][0] - frames[0][0] if len(frames) > 1 else 0
    rate = (len(frames) - 1) / duration if duration > 0 else 0
    tolerance = config.DMX_HZ * 0.1
    check(f"rate ~{config.DMX_HZ} Hz",
          abs(rate - config.DMX_HZ) <= tolerance or len(frames) < 3,
          f"{rate:.1f} Hz over {duration:.2f}s")

    # payload reproduces the programmer
    last = artnet.decode_artdmx(frames[-1][1])
    data = last["data"]
    check("dimmer = 70% -> 178",
          data[0] == 178 and data[4] == 178 and data[8] == 178
          and data[12] == 178, str(list(data[:16])))
    check("colour red in every head",
          data[1] == 255 and data[5] == 255 and data[9] == 255
          and data[13] == 255, str(list(data[:16])))
    check("green/blue at 0",
          data[2] == 0 and data[3] == 0, str(list(data[:8])))

    # Scheduling jitter of the output thread, not protocol correctness:
    # a frame period at 40 Hz is 25 ms, and a loaded machine (server +
    # browser + this test) can easily slip one wakeup by 10-20 ms.  The
    # threshold only has to catch a genuinely stalled/starved loop.
    drift = eng.output.get("drift_ms", -1)
    check("thread drift under 40 ms", drift >= 0 and drift <= 40,
          str(drift))
    check("no output errors", eng.output.get("errors") == 0,
          str(eng.output.get("last_error")))
    check("frames counted", sender.frames_sent > 0,
          str(sender.frames_sent))

    print(f"  --  {len(frames)} frames, {rate:.1f} Hz, drift {drift} ms, "
          f"{sender.frames_sent} sent")
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
