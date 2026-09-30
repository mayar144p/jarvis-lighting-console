"""Jarvis configuration.

Reads jarvis/.env (simple KEY=VALUE file) and applies defaults.
No third-party dependencies.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # jarvis/
WEB = ROOT / "web"
DATA = ROOT / "data"
INBOX = ROOT / "fixtures_inbox"


def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()


def _get(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _bool(key: str, default: str) -> bool:
    return _get(key, default).strip().lower() in ("1", "true", "yes", "on")


# --- the machine's own address on the lighting network -------------------
# Art-Net is not a protocol you can address by broadcast alone.  A node
# with ArtPoll switched on answers a subnet-directed poll from the machine
# that actually holds an IP on that subnet - which is why "send to
# 255.255.255.255" finds a node on the desk's own network and NOT one on
# a laptop that has been moved to a different Wi-Fi, and why a rig that
# worked yesterday stops answering when the DHCP lease changes.
#
# The two addresses people confuse:
#   127.0.0.1          this machine, and only this machine
#   <the LAN address>  the interface the lights are actually on
# So the default has to be the LAN address, discovered rather than
# configured, because the whole point is that the operator should not have
# to know it.

def _lan_ip() -> str:
    """This machine's IPv4 address on the network the lights are on.

    The trick is to ask the OPERATING SYSTEM rather than to enumerate
    interfaces and guess.  A UDP socket is "connected" without sending
    anything, and `getsockname()` then reports the address the kernel
    would actually source from for that destination - which is the answer
    to "which of my interfaces faces this network", not a guess from a
    list.

    `192.0.2.1` is TEST-NET-1 (RFC 5737), reserved and never routed, so
    using it as the destination costs no packet and cannot be captured on
    a real network.  This has been the standard way to find the local
    address for twenty years, and it still works on Windows 11.

    8.8.8.8 is tried first because a machine with a working default route
    will answer with the interface facing THAT route, which is the
    internet-facing one - and on a laptop with both Wi-Fi and Ethernet
    that is the one the lights are usually NOT on.  So the candidate
    addresses are all gathered, and a private one wins over a public one,
    because a lighting rig is on a private network by convention.
    """
    import socket

    def _via(dial_addr: str) -> str | None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.2)
            s.connect((dial_addr, 9))          # UDP connect sends nothing
            addr = s.getsockname()[0]
            return addr if addr and addr != "0.0.0.0" else None
        except OSError:
            return None
        finally:
            s.close()

    candidates: list[str] = []
    for probe in ("8.8.8.8", "192.0.2.1", "2.255.255.255", "10.255.255.255",
                  "172.16.0.1", "192.168.0.1"):
        got = _via(probe)
        if got and got not in candidates:
            candidates.append(got)

    return _pick_lan(candidates)


def _pick_lan(candidates: list[str]) -> str:
    """The Art-Net range first (2.x is what nodes such as Chauvet and
    ENTTEC ship on, and nothing else uses it on a LAN), then a private
    address: a rig lives on 10/8, 172.16/12 or 192.168/16, and a VPN or
    container adapter is the one it is NOT on."""
    for ip in candidates:
        if ip.startswith("2."):
            return ip
    for ip in candidates:
        if _is_private(ip):
            return ip
    return candidates[0] if candidates else ""


def _is_private(ip: str) -> bool:
    """RFC 1918 / CGNAT / link-local, i.e. a network, not the internet."""
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except (ValueError, AttributeError):
        return False
    return (a == 10
            or (a == 172 and 16 <= b <= 31)
            or (a == 192 and b == 168)
            or (a == 169 and b == 254)          # link-local
            or (a == 100 and 64 <= b <= 127))   # CGNAT


LOCAL_IP = _lan_ip()


# --- Branding / server -------------------------------------------------
APP_NAME = _get("APP_NAME", "JARVIS")
APP_SUBTITLE = _get("APP_SUBTITLE", "Lighting Assistant")
APP_LOGO = _get("APP_LOGO", "dog.svg")
HOST = _get("HOST", "127.0.0.1")
PORT = int(_get("PORT", "8787"))

# --- LLM (any OpenAI-compatible endpoint: OpenAI, OpenRouter,
#        Groq, Ollama, LM Studio, ...). Empty key = offline mode. -------
LLM_API_KEY = _get("LLM_API_KEY", "")
LLM_BASE_URL = _get("LLM_BASE_URL",
                    "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
LLM_MODEL = _get("LLM_MODEL", "gemini-3.5-flash-lite")
LLM_TIMEOUT = int(_get("LLM_TIMEOUT", "45"))

# --- Jarvis console engine ---------------------------------------------
# The Jarvis engine is the console: patch, programmer, cues and direct
# Art-Net/sACN output.  There is no second engine and no remote-control
# bridge - the app IS the lighting desk.
# Dry run = frames are built and counted but never reach the network.
CONSOLE_DRY_RUN = _bool("CONSOLE_DRY_RUN", "true")
# Wire protocol for the Jarvis engine.  Both transports carry the same
# 512-slot frame through the SAME sender interface (app/artnet.py
# ArtNetSender / app/sacn.py SacnSender) - fixture, patch, merge and FX
# logic never know which one is underneath.
DMX_TRANSPORT = _get("DMX_TRANSPORT", "artnet").strip().lower()
if DMX_TRANSPORT not in ("artnet", "sacn"):
    DMX_TRANSPORT = "artnet"
# WHERE THE FRAMES GO.
#
# The frames have to reach the NODES, which are other machines on the
# lighting network - so the default can never be this machine's own
# address (unicast to yourself reaches nothing) nor loopback.
#
#   artnet  directed broadcast of the lighting subnet, e.g. 192.168.1.255
#           (2.255.255.255 on the Art-Net 2.x.x.x convention).  Every node
#           on that subnet receives it, and unlike 255.255.255.255 it is
#           routed out of the interface that actually faces the rig.
#   sacn    "multicast": each universe goes to its E1.31 group
#           239.255.<hi>.<lo>, which is what every sACN receiver listens on.
#
# Set DMX_HOST to a node's IP for unicast (best on a busy network).
def directed_broadcast(ip: str) -> str:
    """The broadcast address of the /24 (or Art-Net 2.x /8) holding `ip`."""
    try:
        parts = [int(x) for x in ip.split(".")]
    except (ValueError, AttributeError):
        return "255.255.255.255"
    if len(parts) != 4 or not ip or parts[0] in (0, 127):
        return "255.255.255.255"
    if parts[0] == 2:
        return "2.255.255.255"
    return "%d.%d.%d.255" % tuple(parts[:3])


def _default_dmx_host() -> str:
    if DMX_TRANSPORT == "sacn":
        return "multicast"
    return directed_broadcast(LOCAL_IP) if LOCAL_IP else "255.255.255.255"


DMX_HOST = _get("DMX_HOST", "").strip() or _default_dmx_host()
DMX_HOST_IS_DEFAULT = not os.environ.get("DMX_HOST")
# Port follows the transport unless explicitly overridden:
# Art-Net 6454, sACN/E1.31 5568.
DMX_PORT = int(_get("DMX_PORT",
                    "5568" if DMX_TRANSPORT == "sacn" else "6454"))
DMX_HZ = max(10, min(120, int(_get("DMX_HZ", "40"))))
DMX_NET = max(0, min(127, int(_get("DMX_NET", "0"))))
# true = send blackout frames on shutdown, false = hold the last frame.
DMX_BLACKOUT_ON_EXIT = _bool("DMX_BLACKOUT_ON_EXIT", "false")
# Art-Net: an ArtSync after each tick's frames so several universes change
# together (skipped for a broadcast DMX_HOST, as Art-Net 4 asks).
DMX_SYNC = _bool("DMX_SYNC", "true")
# sACN-only options (ignored by the Art-Net transport).
# 1-63999 = E1.31 synchronisation on that universe; 0 = off.
SACN_SYNC_UNIVERSE = max(0, min(63999, int(_get("SACN_SYNC_UNIVERSE", "0"))))
SACN_PRIORITY = max(0, min(200, int(_get("SACN_PRIORITY", "100"))))
SACN_SOURCE_NAME = _get("SACN_SOURCE_NAME", APP_NAME)
# 32 hex chars; empty = deterministic per-installation CID (see sacn.py).
SACN_CID = _get("SACN_CID", "")

# --- DMX input (Art-Net ArtDmx + sACN E1.31 DATA sniffing) --------------
# Listens on its own sockets in background threads - the control thread
# is never blocked.  DMX_INPUT_MAP remaps source universes on ingest,
# e.g. "1=3,2=3" folds wire universes 1 and 2 into stored universe 3.
DMX_INPUT = _bool("DMX_INPUT", "false")
DMX_INPUT_ARTNET_PORT = int(_get("DMX_INPUT_ARTNET_PORT", "6454"))
DMX_INPUT_SACN_PORT = int(_get("DMX_INPUT_SACN_PORT", "5568"))
# A universe with no packet for this long is reported stale (input loss).
DMX_INPUT_TIMEOUT = float(_get("DMX_INPUT_TIMEOUT", "2.0"))
DMX_INPUT_MAP = _get("DMX_INPUT_MAP", "")

# --- MIDI ---------------------------------------------------------------
# Mappings live in MIDI_MAP (JSON) - notes -> actions, CCs -> parameters.
# With no device present the manager idles gracefully, so it stays on.
MIDI_ENABLED = _bool("MIDI_ENABLED", "true")
MIDI_DEVICE = _get("MIDI_DEVICE", "")             # name substring or index
MIDI_MAP = _get("MIDI_MAP", "")                   # empty = data/midi_map.json if present
CONSOLE_SHOW_DIR = Path(_get("CONSOLE_SHOW_DIR", str(DATA / "shows")))
if not CONSOLE_SHOW_DIR.is_absolute():          # anchor to jarvis/, not the CWD
    CONSOLE_SHOW_DIR = ROOT / CONSOLE_SHOW_DIR
# Autosave the engine state (patch, programmer, playbacks, mode, FX) after
# every successful change to data/autosave.json, and restore it on the
# next start.  Saved shows (SAVE SHOW / LOAD) stay separate and explicit.
CONSOLE_AUTOSAVE = _bool("CONSOLE_AUTOSAVE", "true")
CONSOLE_AUTORESTORE = _bool("CONSOLE_AUTORESTORE", "true")
# Token for the control API.  Empty is only safe on a loopback bind; on
# any other bind a token is REQUIRED (see config.requires_token and
# Handler._authorised), because /api/console can put real DMX on a wire.
CONSOLE_TOKEN = _get("CONSOLE_TOKEN", "").strip()


def requires_token() -> bool:
    """True when the server is reachable from off this machine."""
    return HOST not in ("127.0.0.1", "localhost", "::1")

# --- Fixture database ---------------------------------------------------
DB_PATH = Path(_get("FIXTURE_DB", str(DATA / "fixtures.db")))
# Seed the built-in generic profiles (4 shapes) and the curated profile
# library into the fixture database on start.  Turn this OFF to run with
# only what the GDTF Share brought in - which is what you want when you
# own real fixtures, since a generic's channel map is a guess and a wrong
# guess silently mis-addresses a light.
#
# The trade-off is real: with seeding off, NOTHING can be patched while
# the Share is unreachable, and the add-heads list is empty until you
# have fetched something.  The channel definitions themselves are NOT
# affected - app/profiles.py is an in-code registry read for defaults and
# ranges, and a patched head carries its own channel map, so heads that
# are already on the patch keep working either way.
FIXTURE_SEED_BUILTINS = _bool("FIXTURE_SEED_BUILTINS", "false")

# --- GDTF Share (https://gdtf-share.com) --------------------------------
# The official fixture database, fetched on demand instead of hunting for
# .gdtf files by hand.  The API needs a free account; a login hands back a
# 2-hour session cookie, so the password is only needed at sign-in.
# Credentials here are for unattended use (a show machine with no browser
# prompt).  Leave them empty and sign in through the console instead - the
# password is then held in the server process's memory only and is never
# written to disk.
GDTF_SHARE_USER = _get("GDTF_SHARE_USER", "").strip()
GDTF_SHARE_PASSWORD = _get("GDTF_SHARE_PASSWORD", "")
GDTF_SHARE_CACHE = Path(_get("GDTF_SHARE_CACHE", str(DATA / "gdtf_share")))
if not GDTF_SHARE_CACHE.is_absolute():
    GDTF_SHARE_CACHE = ROOT / GDTF_SHARE_CACHE
GDTF_SHARE_TIMEOUT = float(_get("GDTF_SHARE_TIMEOUT", "20"))

DATA.mkdir(parents=True, exist_ok=True)


def logo_path() -> Path | None:
    """Find APP_LOGO in web/, jarvis/ or the project folder above jarvis/."""
    for folder in (WEB, ROOT, ROOT.parent):
        candidate = folder / APP_LOGO
        if candidate.is_file():
            return candidate
    return None


def status() -> dict:
    """Public status for the UI."""
    return {
        "app": APP_NAME,
        "subtitle": APP_SUBTITLE,
        "logo": APP_LOGO,
        "llm_configured": bool(LLM_API_KEY),
        "model": LLM_MODEL,
        "base_url": LLM_BASE_URL,
        "gdtf_share": {
            # Never the password, and never "is the password right" - just
            # whether an account is configured at all.
            "configured": bool(GDTF_SHARE_USER and GDTF_SHARE_PASSWORD),
            "user": GDTF_SHARE_USER,
        },
        "console": {
            "dry_run": CONSOLE_DRY_RUN,
            "token_required": requires_token() and not CONSOLE_TOKEN,
            "transport": DMX_TRANSPORT,
            "host": DMX_HOST,
            # So the UI can say "going to your machine's address" rather than
            # leaving the operator to work out why 255.255.255.255 finds
            # nothing.  `local_ip` is what was detected; `host_is_default`
            # says whether that is what is actually in use.
            "local_ip": LOCAL_IP,
            "host_is_default": DMX_HOST_IS_DEFAULT,
            "broadcast": DMX_HOST == "255.255.255.255"
                         or DMX_HOST.endswith(".255"),
            "multicast": DMX_HOST == "multicast",
            "port": DMX_PORT,
            "hz": DMX_HZ,
            "net": DMX_NET,
            "sacn_priority": SACN_PRIORITY,
            "sync": DMX_SYNC,
            "sacn_sync_universe": SACN_SYNC_UNIVERSE,
            "autosave": CONSOLE_AUTOSAVE,
            "autorestore": CONSOLE_AUTORESTORE,
            "dmx_input": DMX_INPUT,
            "midi": MIDI_ENABLED,
        },
    }
