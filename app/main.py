"""Jarvis desktop server - stdlib only.

Run:  python app/main.py   (or double-click run.bat)
Then: http://localhost:8787
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hmac
from urllib.parse import parse_qs, urlparse

# Works both as `python app/main.py` and `python -m app.main`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import (artnet, autoshow, config, console_ai, dmxin, doctor, fixlib, manual,  # noqa: E402
                 fixture_kind, fixtures, gdtf_geom, gdtfshare, midi, profiles, rdm)
from app import engine as engine_mod  # noqa: E402
from app.engine_support import channel_role  # noqa: E402
from tools import import_gdtf  # noqa: E402

# GDTF archives arrive as base64 in a JSON body; a big profile is a few MB.
MAX_BODY = 30 * 1024 * 1024

_MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".woff2": "font/woff2", ".glb": "model/gltf-binary",
    ".txt": "text/plain; charset=utf-8", ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}

# One GDTF Share client for the process.  It owns the session cookie, so
# it has to be a singleton: a per-request client would drop the 2-hour
# login every call and the operator would be asked to sign in again on
# every search.  Created lazily so importing main does not touch disk.
_SHARE: gdtfshare.GdtfShare | None = None


def gdtf_share() -> gdtfshare.GdtfShare:
    global _SHARE
    if _SHARE is None:
        _SHARE = gdtfshare.GdtfShare(
            config.DB_PATH, config.GDTF_SHARE_CACHE,
            user=config.GDTF_SHARE_USER,
            password=config.GDTF_SHARE_PASSWORD,
            timeout=config.GDTF_SHARE_TIMEOUT)
    return _SHARE


# Floor plans (an image, or a PDF page the browser rendered to PNG) are
# stored content-addressed beside the show data; the venue keeps only the id.
_UNDERLAY_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}
_UNDERLAY_MAX = 15 * 1024 * 1024


def _underlay_dir() -> Path:
    return config.DATA / "underlays"


def save_underlay(body: dict) -> dict:
    raw = str(body.get("data") or "")
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"floor plan is not valid base64: {exc}") from exc
    if not data or len(data) > _UNDERLAY_MAX:
        raise ValueError("floor plan must be an image under 15 MB")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    elif data[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ext = ".webp"
    else:
        raise ValueError("floor plan must be PNG, JPEG or WebP "
                         "(a PDF is rendered to an image in the browser)")
    ident = hashlib.sha256(data).hexdigest()[:32]
    folder = _underlay_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (ident + ext)).write_bytes(data)
    return {"id": ident, "bytes": len(data), "type": ext[1:]}


# Show audio for the timeline, stored the same way.
_AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
                ".m4a": "audio/mp4", ".flac": "audio/flac"}


def save_audio(body: dict) -> dict:
    raw = str(body.get("data") or "")
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"audio is not valid base64: {exc}") from exc
    if not data or len(data) > MAX_BODY:
        raise ValueError("audio must be under 22 MB")
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        ext = ".mp3"
    elif data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        ext = ".wav"
    elif data[:4] == b"OggS":
        ext = ".ogg"
    elif data[:4] == b"fLaC":
        ext = ".flac"
    elif data[4:8] == b"ftyp":
        ext = ".m4a"
    else:
        raise ValueError("audio must be MP3, WAV, OGG, FLAC or M4A")
    ident = hashlib.sha256(data).hexdigest()[:32]
    folder = config.DATA / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (ident + ext)).write_bytes(data)
    return {"id": ident, "bytes": len(data), "type": ext[1:]}


def audio_path(ident: str) -> Path | None:
    ident = "".join(ch for ch in ident if ch.isalnum())[:64]
    if not ident:
        return None
    for ext in _AUDIO_TYPES:
        p = config.DATA / "audio" / (ident + ext)
        if p.is_file():
            return p
    return None


def underlay_path(ident: str) -> Path | None:
    ident = "".join(ch for ch in ident if ch.isalnum())[:64]
    if not ident:
        return None
    for ext in _UNDERLAY_TYPES:
        p = _underlay_dir() / (ident + ext)
        if p.is_file():
            return p
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "Jarvis/0.1"
    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        """Swallow connection resets (browser tabs closing / server stop)."""
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass

    # -- helpers ---------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # quieter than default
        if "/api/" in (args[0] if args else ""):
            print(f"[http] {self.address_string()} {fmt % args}")

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("payload too large (max 30 MB)")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _json_bytes(self, data: bytes, ext: str = "") -> None:
        """A GDTF model file, typed by its extension."""
        mime = {
            ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
            ".3ds": "application/octet-stream", ".obj": "text/plain",
            ".stl": "model/stl",
        }.get((ext or "").lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        # Content-addressed URLs: the bytes never change.
        self.send_header("Cache-Control", "public, max-age=604800")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path, status: int = 200) -> None:
        """Serve a static file; `no-cache` + ETag so clients revalidate."""
        if not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        # Explicit web types: Windows registry often maps .js to text/plain.
        mime = _MIME.get(path.suffix.lower()) or \
            mimetypes.guess_type(str(path))[0]
        etag = '"%s-%d"' % (hashlib.sha256(data).hexdigest()[:16], len(data))
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self._security_headers()
            self.end_headers()
            return
        self.send_response(status)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _security_headers(self) -> None:
        """Headers on every response (200 and 304 alike)."""
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    # -- auth ------------------------------------------------------------
    def _authorised(self) -> bool:
        """Token check (constant time, header only - never the query string).

        Loopback binds need no token; a network bind requires one.
        """
        token = config.CONSOLE_TOKEN
        if not token:
            return not config.requires_token()
        return hmac.compare_digest(self.headers.get("X-Jarvis-Token") or "", token)

    # Everything under /api/ needs the token unless it is listed here: a new
    # endpoint is private until deliberately made public.  These two are
    # read-only and let the shell render before a token is entered.
    PUBLIC_API_GET = frozenset({"/api/status", "/api/fixtures"})

    def _needs_auth(self, route: str, method: str) -> bool:
        if not route.startswith("/api/"):
            return False        # a static file; reads nothing sensitive
        if method == "GET" and route in self.PUBLIC_API_GET:
            return False
        return True

    LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

    def _host_name(self, value: str) -> str:
        value = (value or "").strip().lower()
        if value.startswith("["):                      # [::1]:8787
            return value[:value.find("]") + 1]
        return value.rsplit(":", 1)[0] if value.count(":") == 1 else value

    def _same_origin(self, method: str) -> str | None:
        """Refuse requests a web page on another site could have made.

        Two attacks reach a desk on 127.0.0.1 with no token at all:

        * cross-site requests - any page the operator visits can POST a
          `text/plain` body at localhost, which the browser sends without
          asking.  So a write must be `application/json` (which forces a
          CORS preflight this server never approves) and, when the browser
          names an Origin, that origin must be this server.
        * DNS rebinding - a hostile name that resolves to 127.0.0.1 makes
          the browser treat the desk as that site.  On a loopback bind the
          Host header must therefore be a loopback name.

        Returns the reason to refuse, or None.
        """
        host = self._host_name(self.headers.get("Host", ""))
        if not config.requires_token() and host and \
                host not in self.LOOPBACK_HOSTS:
            return "unexpected Host header"
        origin = self.headers.get("Origin")
        if origin and origin != "null":
            o_host = self._host_name(urlparse(origin).netloc)
            if o_host != host or urlparse(origin).port != \
                    urlparse("//" + (self.headers.get("Host") or "")).port:
                return "cross-origin request refused"
        elif origin == "null":
            return "cross-origin request refused"
        if method == "POST":
            ctype = (self.headers.get("Content-Type") or "").split(";")[0]
            if ctype.strip().lower() != "application/json":
                return "requests must be application/json"
        return None

    def _deny(self) -> None:
        self._json({"error": "unauthorised - set CONSOLE_TOKEN in .env, "
                            "or bind to 127.0.0.1"}, 401)

    # -- routing ---------------------------------------------------------
    def do_GET(self) -> None:
        url = urlparse(self.path)
        route = url.path

        if route in ("/", "/index.html", "/console.html"):
            return self._file(config.WEB / "index.html")
        if route == "/" + config.APP_LOGO:
            logo = config.logo_path()
            return self._file(logo) if logo else self._json({"error": "not found"}, 404)
        if route.startswith("/api/"):
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            refused = self._same_origin("GET")
            if refused:
                return self._json({"error": refused}, 403)
            if self._needs_auth(route, "GET") and not self._authorised():
                return self._deny()
            try:
                return self._api_get(route, query)
            except (ValueError, TypeError, KeyError) as exc:
                return self._json({"error": f"bad request: {exc}"}, 400)
            except Exception as exc:  # noqa: BLE001 - surface to the UI
                return self._json({"error": str(exc)}, 500)

        candidate = (config.WEB / route.lstrip("/")).resolve()
        if config.WEB.resolve() in candidate.parents or candidate == config.WEB.resolve():
            return self._file(candidate)
        self._json({"error": "forbidden"}, 403)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        route = url.path
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        refused = self._same_origin("POST")
        if refused:
            return self._json({"error": refused}, 403)
        if self._needs_auth(route, "POST") and not self._authorised():
            return self._deny()
        try:
            body = self._read_body()
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)

        try:
            if route == "/api/fixtures/channel":
                # A library change: re-map patched heads so it applies now.
                try:
                    done = fixtures.set_channel_label(
                        config.DB_PATH, int(body["mode_id"]),
                        int(body["channel"]) - 1, str(body.get("label", "")))
                except (KeyError, TypeError, ValueError) as exc:
                    return self._json({"error": str(exc)}, 400)
                fixtures.invalidate_cache()
                remapped = self._engine().remap_heads()
                return self._json({**done, "remapped": remapped})
            if route == "/api/fixtures/range":
                try:
                    done = fixtures.set_channel_range(
                        config.DB_PATH, int(body["mode_id"]),
                        int(body["channel"]) - 1,
                        body.get("min"), body.get("max"))
                except (KeyError, TypeError, ValueError) as exc:
                    return self._json({"error": str(exc)}, 400)
                fixtures.invalidate_cache()
                remapped = self._engine().remap_heads()
                return self._json({**done, "remapped": remapped})
            if route == "/api/fixtures/delete":
                try:
                    gone = fixtures.delete(config.DB_PATH, int(body.get("id")))
                except (TypeError, ValueError):
                    return self._json({"error": "id is required"}, 400)
                if gone is None:
                    return self._json({"error": "no such fixture"}, 404)
                fixtures.invalidate_cache()
                patched = [h["head_no"] for h in self._engine().patch
                           if (h.get("manufacturer"), h.get("model")) == (gone["manufacturer"], gone["model"])]
                return self._json({**gone, "patched": patched})
            if route == "/api/fixtures/create":
                try:
                    made = fixtures.create_profile(
                        config.DB_PATH, str(body.get("manufacturer", "")),
                        str(body.get("model", "")),
                        str(body.get("mode", "Default")),
                        [str(c) for c in (body.get("channels") or [])],
                        body.get("ranges") or None)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                fixtures.invalidate_cache()
                return self._json(made)
            if route == "/api/fixtures/import":
                summary = import_gdtf.run(config.DB_PATH, config.INBOX)
                return self._json(summary)
            if route == "/api/gdtf/login":
                # Credentials stay in memory only; a wrong password is a normal answer.
                client = gdtf_share()
                client.set_credentials(str(body.get("user", "")),
                                       str(body.get("password", "")))
                try:
                    return self._json({**client.login(), **client.status()})
                except gdtfshare.GdtfShareError as exc:
                    return self._json({"error": exc.message, "code": exc.code,
                                       **client.status()}, 200)
            if route == "/api/gdtf/logout":
                return self._json(gdtf_share().logout())
            if route == "/api/fixtures/from_manual":
                # A manual's DMX chart -> a DRAFT for the operator to check;
                # nothing is stored until they save it.
                try:
                    draft = manual.read(str(body.get("text", "")),
                                        str(body.get("manufacturer", "")),
                                        str(body.get("model", "")),
                                        offline=bool(body.get("offline")))
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"draft": draft})
            if route == "/api/fixtures/from_manual/save":
                try:
                    parsed = manual.to_parsed(body.get("draft") or {})
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                item = parsed[0]
                slug = re.sub(r"[^a-z0-9]+", "-", f"{item['manufacturer']} {item['model']}".lower()).strip("-")
                done = fixtures.store_parsed(config.DB_PATH, parsed, f"manual:{slug}")
                fixtures.invalidate_cache()
                engine_mod._FIXTURE_CACHE.clear()
                first = (done.get("imported") or [{}])[0]
                fixture = fixtures.get(config.DB_PATH, int(first["fixture_id"])) \
                    if first.get("fixture_id") else None
                return self._json({"fixture": fixture,
                                   "summary": f"saved {item['manufacturer']} {item['model']} to your library"})
            if route == "/api/fixtures/library/install":
                # A bundled OFL / QLC+ fixture into the installed library.
                src, key = str(body.get("src", "")), str(body.get("key", ""))
                try:
                    done = fixtures.store_parsed(config.DB_PATH, fixlib.load(src, key),
                                                 f"{src}:{key}")
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                fixtures.invalidate_cache()
                engine_mod._FIXTURE_CACHE.clear()
                first = (done.get("imported") or [{}])[0]
                item = fixtures.get(config.DB_PATH, int(first["fixture_id"])) \
                    if first.get("fixture_id") else None
                return self._json({"fixture": item, "summary":
                                   f"installed {first.get('manufacturer', '')} "
                                   f"{first.get('model', '')} from the "
                                   f"{fixlib.SOURCES[src]['name']}"})
            if route == "/api/gdtf/download":
                client = gdtf_share()
                try:
                    done = client.download(body.get("rid"))
                except gdtfshare.GdtfShareError as exc:
                    return self._json({"error": exc.message, "code": exc.code,
                                       **client.status()}, 200)
                # Drop every cached view of the library and its geometry.
                fixtures.invalidate_cache()
                engine_mod._FIXTURE_CACHE.clear()
                gdtf_geom.clear_manifest_cache()
                return self._json({**done, **client.status()})
            if route in ("/api/console", "/api/console/patch",
                         "/api/console/import_show",
                         "/api/console/save", "/api/console/load",
                         "/api/console/scan", "/api/console/ai",
                         "/api/console/generate", "/api/console/midi",
                         "/api/console/underlay", "/api/console/audio",
                         "/api/console/autoshow", "/api/console/rdm",
                         "/api/console/look"):
                return self._console_post(route, body, query)
        except Exception as exc:  # noqa: BLE001 - surface to the UI
            return self._json({"error": str(exc)}, 500)

        self._json({"error": "unknown endpoint"}, 404)

    # -- console --------------------------------------------------------
    @staticmethod
    def _engine():
        if engine_mod.ENGINE is None:
            raise RuntimeError("engine not initialised")
        return engine_mod.ENGINE

    def _console_result(self, eng, result: dict) -> None:
        self._json({"result": result, "mode": eng.mode,
                    "dry_run": eng.dry_run, "live": eng.live,
                    "actions": sorted(engine_mod.ACTIONS)})

    def _console_post(self, route: str, body: dict,
                      query: dict | None = None) -> None:
        eng = self._engine()
        query = query or {}
        if route == "/api/console":
            params = body.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            return self._console_result(
                eng, eng.act(str(body.get("action", "")), **params))
        if route == "/api/console/underlay":
            return self._json(save_underlay(body))
        if route == "/api/console/audio":
            return self._json(save_audio(body))
        if route == "/api/console/look":
            # /api/console/look is a GET (it is a feed, polled at 20 Hz);
            # accept POST too so a caller that posts every console route
            # does not get a 404.  The handler is the GET one, above.
            url = urlparse(self.path)
            return self._api_get(route, {k: v[0] for k, v
                                         in parse_qs(url.query).items()})
        if route == "/api/console/patch":
            # `from_layout` went with the rig studio.  It could only ever
            # have succeeded after a layout had been generated, and the
            # studio was the only thing that generated one - so the action
            # became a door to nothing and the route would have answered
            # "no layout generated yet" forever.  A dead branch that looks
            # like a feature is worse than a missing one.
            action = str(body.get("action", "from_csv"))
            if action == "from_csv":
                result = eng.act("patch_from_csv",
                                 csv=str(body.get("csv", "")))
            elif action == "clear":
                result = eng.act("patch_clear")
            else:
                raise ValueError(f"unknown patch action {action!r}")
            return self._console_result(eng, result)
        if route == "/api/console/import_show":
            result = eng.act("import_show",
                             concept=body.get("concept"),
                             playback=body.get("playback"),
                             name=str(body.get("name", "")))
            return self._console_result(eng, result)
        if route == "/api/console/save":
            result = eng.act("save_show", name=str(body.get("name", "")))
            return self._console_result(eng, result)
        if route == "/api/console/load":
            result = eng.act("load_show", name=str(body.get("name", "")))
            return self._console_result(eng, result)
        if route == "/api/console/rdm":
            # RDM: ask the lights what they are.  Network I/O off the lock,
            # then the engine lines the answers up with the patch.
            net = eng.network_info()
            host = net["target"]["host"] if net["target"]["mode"] != "auto" and net["target"]["host"] \
                else "255.255.255.255"
            if isinstance(body.get("set_address"), dict):
                sa = body["set_address"]
                try:
                    res = rdm.set_address(str(sa.get("uid") or ""), int(sa.get("universe") or 1),
                                          int(sa.get("address") or 0), host=host, port=config.DMX_PORT,
                                          net=config.DMX_NET)
                except ValueError as exc:
                    res = {"ok": False, "error": str(exc)}
                return self._console_result(eng, res)
            universes = body.get("universes") or sorted({h["universe"] for h in eng.patch}) or [1]
            found = rdm.discover([int(u) for u in universes], host=host, port=config.DMX_PORT,
                                 net=config.DMX_NET, timeout=float(body.get("timeout") or 2.0))
            result = eng.act("rdm_compare", devices=found["devices"], universes=found.get("universes"))
            result["rdm_error"] = found.get("error")
            result["tried"] = found.get("tried")
            return self._console_result(eng, result)
        if route == "/api/console/scan":
            # Network I/O runs OUTSIDE the engine lock: act() holds the
            # engine RLock for its whole call, so sniff the wire first,
            # then hand the observed universes to import_scan.
            #
            # sweep (on by default) is what makes a negative result mean
            # something: when the broadcast pass finds nothing, every
            # address on the local /24 is polled by unicast, because Wi-Fi
            # APs and client isolation drop broadcast silently and nothing
            # on the network reports it.
            def _scan_num(key, default):
                try:
                    return float(body.get(key) or default)
                except (TypeError, ValueError):
                    return float(default)
            try:
                scan_port = int(body["port"]) if body.get("port") else config.DMX_PORT
            except (TypeError, ValueError):
                scan_port = config.DMX_PORT
            # Poll every adapter's own broadcast too (255.255.255.255
            # leaves by the default route only), and the node in use.
            net = eng.network_info()
            targets = [i["broadcast"] for i in net["interfaces"]]
            if net["target"]["mode"] != "auto" and net["target"]["host"]:
                targets.append(net["target"]["host"])
            found = artnet.scan(
                timeout=_scan_num("timeout", 1.5),
                port=scan_port,
                net=config.DMX_NET,
                sweep_subnets=body.get("sweep", True) is not False,
                sweep_timeout=_scan_num("sweep_timeout", 1.5),
                targets=targets)
            limit = int(getattr(engine_mod, "MAX_UNIVERSES", 4096))
            observed = [{"universe": row["universe"],
                         "channels": row["channels"]}
                        for row in found["universes"]
                        if 1 <= row["universe"] <= limit]
            result = {
                "universes": found["universes"],   # full truth for the UI
                "nodes": found["nodes"],
                "scan_error": found["error"],
                "polls_sent": found["polls_sent"],
                "replies": found["replies"],
                "frames": found["frames"],
                "tried": found.get("tried", []),
                "swept": found.get("swept", 0),
                "subnets": found.get("subnets", []),
                "import": None,
            }
            if body.get("import") is False:
                result["message"] = found.get("message", "")
                return self._console_result(eng, result)   # sniff only
            if not observed:
                result["message"] = (found.get("message")
                                     or found["error"]
                                     or "no Art-Net traffic observed")
                return self._console_result(eng, result)
            # Auto-patch what was seen; universes already patched are
            # skipped by the engine, so a rescan never disturbs a rig.
            result["import"] = eng.act(
                "import_scan", observed=observed,
                query=str(body.get("query", "") or ""))
            return self._console_result(eng, result)
        if route == "/api/console/midi":
            # Device selection + live mapping reload.  Never raises: with
            # no device present the result carries the graceful error.
            mgr = midi.MANAGER
            if mgr is None:
                result = {"ok": False, "midi": midi.status(),
                          "error": "MIDI is disabled (MIDI_ENABLED=false)"}
                return self._console_result(eng, result)
            result = {"ok": True}
            if body.get("device") is not None:
                mgr.stop()                       # close the old device first
                mgr.device = body.get("device")
                mgr.status["device"] = None
                if not mgr.start():
                    result["ok"] = False
                    result["error"] = mgr.status.get("error")
            if body.get("reload"):
                result["map"] = mgr.reload_map(body.get("map_path"))
                if not result["map"].get("ok"):
                    result["ok"] = False
            result["midi"] = midi.status()
            return self._console_result(eng, result)
        if route == "/api/console/ai":
            # Compile only unless the operator explicitly applies: the
            # plan-first shape keeps "ask before changing anything".
            if isinstance(body.get("steps"), list):
                # Apply the previewed steps exactly (re-validated, no second model call).
                result = {**console_ai._validate(
                    {"reply": body.get("reply") or "OK.",
                     "steps": body["steps"]}), "source": "preview"}
            else:
                result = console_ai.plan(str(body.get("message", "")),
                                         offline=bool(body.get("offline")),
                                         eng=eng, history=body.get("history"))
            if body.get("apply"):
                try:
                    calls = console_ai.resolve(result["steps"], eng)
                    result["run"] = console_ai.run(calls, eng)
                except ValueError as exc:      # bad target in a step
                    result["run"] = {"ok": False, "executed": 0,
                                     "steps_run": [], "error": str(exc)}
                if result["run"].get("ok"):
                    result["reply"] = (
                        f"{result['reply']} [ran "
                        f"{result['run']['executed']} step(s)]")
                else:
                    result["reply"] = (
                        f"{result['reply']} [failed: "
                        f"{result['run'].get('error')}]")
            return self._console_result(eng, result)
        if route == "/api/console/autoshow":
            # Preview a whole-show design for this rig, or build a design
            # the operator has already seen (never a second model call).
            playback = int(body.get("playback") or 1)
            if body.get("apply") and isinstance(body.get("design"), dict):
                return self._console_result(eng, autoshow.build(eng, body["design"], playback))
            return self._console_result(eng, autoshow.design(
                eng, str(body.get("prompt", "")), offline=bool(body.get("offline"))))
        if route == "/api/console/generate":
            # Brief -> 2-3 concepts; the engine is only touched when the
            # operator confirms via /api/console/import_show.
            result = console_ai.generate(
                str(body.get("prompt", "")),
                variant=int(body.get("variant", 0) or 0),
                offline=bool(body.get("offline")), eng=eng)
            return self._console_result(eng, result)

    def _stream(self) -> None:
        """One live connection instead of three polling loops.

        Server-sent events: `snapshot` (the full state) on connect and after
        every edit, `lite` (fast-changing state: faders, output, playback
        position) five times a second, and `look` (the light, for the 3D
        stage) up to 30 times a second when it changes.
        """
        import time as _time
        eng = self._engine()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self._security_headers()
        self.end_headers()
        self.close_connection = True

        def send(event: str, data) -> None:
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            self.wfile.write(f"event: {event}\ndata: {body}\n\n".encode("utf-8"))
            self.wfile.flush()

        rev = None
        sent_parts: dict[str, str] | None = None    # what this screen has
        last_look = None
        next_lite = 0.0
        next_beat = 0.0
        next_snap = 0.0
        try:
            while True:
                now = _time.monotonic()
                # At most four snapshots a second: a fader drag is sixty
                # edits, and the lite event already carries its value.
                if eng.act_rev != rev and now >= next_snap:
                    next_snap = now + 0.25
                    rev = eng.act_rev
                    snap = eng.snapshot()
                    snap["actions"] = sorted(engine_mod.ACTIONS)
                    parts = {k: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
                             for k, v in snap.items()}
                    if sent_parts is None:
                        body = "{" + ",".join(json.dumps(k) + ":" + t for k, t in parts.items()) + "}"
                        self.wfile.write(f"event: snapshot\ndata: {body}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    else:
                        # only the parts that changed: an edit to one cue
                        # is not 200 KB to every screen
                        changed = [k for k, t in parts.items() if sent_parts.get(k) != t]
                        gone = [k for k in sent_parts if k not in parts]
                        if changed or gone:
                            body = ('{"set":{' + ",".join(json.dumps(k) + ":" + parts[k] for k in changed)
                                    + '},"del":' + json.dumps(gone) + "}")
                            self.wfile.write(f"event: snapdiff\ndata: {body}\n\n".encode("utf-8"))
                            self.wfile.flush()
                    sent_parts = parts
                if now >= next_lite:
                    next_lite = now + 0.1
                    lite = eng.lite(eng.patch_rev)
                    lite.pop("heads", None)
                    send("lite", lite)
                text = eng.look_text()          # shared by every screen
                if text != last_look:
                    last_look = text
                    self.wfile.write(("event: look\ndata: " + text + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                if now >= next_beat:
                    next_beat = now + 10
                    self.wfile.write(b": beat\n\n")
                    self.wfile.flush()
                _time.sleep(1 / 30)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _api_get(self, route: str, query: dict) -> None:
        if route == "/api/status":
            status = config.status()
            status["fixtures"] = fixtures.count(config.DB_PATH)
            status["actions"] = sorted(engine_mod.ACTIONS)
            if engine_mod.ENGINE is not None:
                # runtime truth - config only holds the env defaults
                status["console"]["dry_run"] = engine_mod.ENGINE.dry_run
                status["console"]["live"] = engine_mod.ENGINE.live
                status["console"]["patched"] = len(engine_mod.ENGINE.patch)
                status["console"]["selected"] = len(engine_mod.ENGINE.selected)
            status["dmx_input"] = dmxin.snapshot()   # observable input state
            status["midi"] = midi.status()           # devices / open / errors
            return self._json(status)
        if route == "/api/console/input":
            # DMX input observability: frames, per-universe age + staleness.
            return self._json(dmxin.snapshot())
        if route == "/api/console/profiles":
            # Rich fixture definitions (functions, ranges, defaults, bits).
            return self._json({"profiles": [profiles.export(p)
                                            for p in profiles.PROFILES]})
        if route == "/api/console/channels":
            # The DMX truth for the selection (on demand, not on the live feed).
            heads = None
            if query.get("heads"):
                try:
                    heads = [int(x) for x in query["heads"].split(",") if x.strip()]
                except ValueError:
                    return self._json({"error": "heads must be a list of numbers"},
                                      400)
            return self._json(self._engine().channel_report(heads))
        if route == "/api/fixtures/profile":
            # The fixture editor record: modes, footprints, labels and resolved roles.
            fid = query.get("id")
            if not fid:
                return self._json({"error": "id is required"}, 400)
            try:
                row = fixtures.get(config.DB_PATH, int(fid))
            except (TypeError, ValueError):
                return self._json({"error": "id must be a number"}, 400)
            if row is None:
                return self._json({"error": "no such fixture"}, 404)
            modes = fixtures.list_modes(config.DB_PATH, int(fid))
            for m in modes:
                detail = m.get("detail") or []
                m["roles"] = []
                for i, c in enumerate(m["channels"]):
                    d = detail[i] if i < len(detail) else {}
                    role = channel_role(c)
                    m["roles"].append({
                        "role": role,
                        "controllable": role not in ("raw", "unused"),
                        "min": (d or {}).get("phys_from"),
                        "max": (d or {}).get("phys_to"),
                    })
                m["uncontrollable"] = sum(1 for r in m["roles"]
                                          if not r["controllable"])
            return self._json({
                "id": row["id"], "manufacturer": row["manufacturer"],
                "model": row["model"], "source": row.get("source"),
                "modes": modes,
                "assignable": list(fixtures.ASSIGNABLE_ROLES),
                "role_label": fixtures.ROLE_LABEL,
            })
        if route == "/api/console/attributes":
            # What the selection can do, paged, with every value visible.
            heads = None
            if query.get("heads"):
                try:
                    heads = [int(x) for x in query["heads"].split(",") if x.strip()]
                except ValueError:
                    return self._json({"error": "heads must be a list of numbers"},
                                      400)
            return self._json(self._engine().attribute_state(heads))
        if route == "/api/console/capabilities":
            # What the selection can do, and which attributes it cannot.
            heads = None
            if query.get("heads"):
                try:
                    heads = [int(x) for x in query["heads"].split(",") if x.strip()]
                except ValueError:
                    return self._json({"error": "heads must be a list of numbers"},
                                      400)
            return self._json(self._engine().capabilities(heads))
        if route == "/api/console/stream":
            return self._stream()
        if route == "/api/console/doctor":
            return self._json(doctor.examine(self._engine()))
        if route == "/api/console/look":
            # Per-tick light feed; `since` is a hint, unknown seq returns a full snapshot.
            try:
                since = int(query.get("since", ""))
            except ValueError:
                since = None
            return self._json(self._engine().look_feed(since))
        if route == "/api/console/models":
            # One manifest entry per definition in the patch, shared by its heads.
            eng = self._engine()
            out = config.DATA / "gdtf_models"
            seen, defs = set(), []
            for h in eng.patch:
                row = dict(h, source=eng.model_source(h))
                did = gdtf_geom.definition_id(row)
                if did in seen:
                    continue
                seen.add(did)
                src = row["source"]
                built = gdtf_geom.manifest_for(
                    row, config.GDTF_SHARE_CACHE, out,
                    manifest_of=(src if src and src.lower().endswith(".gdtf")
                                 else None))
                pub = gdtf_geom.public_manifest(built)
                pub["heads"] = [x["head_no"] for x in eng.patch
                                if gdtf_geom.definition_id(
                                    dict(x, source=eng.model_source(x))) == did]
                pub["summary"] = gdtf_geom.summarise(built)
                defs.append(pub)
            return self._json({"definitions": defs, "count": len(defs)})

        if route == "/api/console/audio":
            path = audio_path(str(query.get("id", "")))
            if not path:
                return self._json({"error": "no such audio"}, 404)
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _AUDIO_TYPES[path.suffix])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=604800")
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)
            return None
        if route == "/api/console/network":
            # the adapters, the output target and whether it is reachable
            return self._json(self._engine().network_info())
        if route == "/api/console/underlay":
            path = underlay_path(str(query.get("id", "")))
            if not path:
                return self._json({"error": "no such floor plan"}, 404)
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _UNDERLAY_TYPES[path.suffix])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=604800")
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)
            return None
        if route == "/api/console/model":
            # One model file, fetched once per definition and cached by the browser.
            did = str(query.get("id", "")).strip()
            name = str(query.get("name", "")).strip()
            if not did or not name:
                return self._json({"error": "id and name are required"}, 400)
            built = gdtf_geom.manifest_for(
                {"source": did + ".gdtf", "manufacturer": "", "model": did,
                 "mode": "", "map": ["pan", "tilt"]},
                config.GDTF_SHARE_CACHE, config.DATA / "gdtf_models")
            # Match the archive-relative name exactly (<Model File> differs from its
            # Name), which is also the path-safety check: only extracted files match.
            entry = None
            for cand in (built.get("files") or {}).values():
                if cand.get("name") == name:
                    entry = cand
                    break
            if not entry:
                return self._json({"error": "no such model in this definition"},
                                  404)
            try:
                data = Path(entry["path"]).read_bytes()
            except OSError as exc:
                return self._json({"error": "model unreadable: %s" % exc}, 500)
            return self._json_bytes(data, entry["ext"])
        if route == "/api/console":
            eng = self._engine()
            if query.get("lite"):
                try:
                    rev = int(query.get("rev", ""))
                except ValueError:
                    rev = None
                return self._json(eng.lite(rev))
            return self._json(eng.snapshot())
        if route == "/api/fixtures":
            rows = fixtures.search(config.DB_PATH, query.get("q", ""), limit=20, fuzzy=True)
            for r in rows:
                # What the light physically is, so the picker can show the
                # 3D model before anything is patched.
                mode = engine_mod.default_mode(r.get("modes") or []) or {}
                r["default_mode"] = mode.get("name")
                r["body"] = fixture_kind.describe({
                    "manufacturer": r.get("manufacturer"), "model": r.get("model"),
                    "mode": mode.get("name", ""),
                    "map": [channel_role(c) for c in mode.get("channels") or []]})
            return self._json({"results": rows})
        if route == "/api/fixtures/library":
            # The bundled open libraries, searched offline.
            try:
                limit = max(1, min(200, int(query.get("limit", 60))))
            except ValueError:
                return self._json({"error": "limit must be a number"}, 400)
            rows = fixlib.search(query.get("q", ""), limit)
            for r in rows:
                r["body"] = fixture_kind.describe({
                    "manufacturer": r["manufacturer"], "model": r["model"],
                    "mode": (r["modes"] or [["", 0]])[0][0], "map": []})
            return self._json({"results": rows, "libraries": fixlib.libraries()})
        if route == "/api/gdtf/status":
            # Never raises; the status distinguishes signed-out, unreachable and empty.
            return self._json(gdtf_share().status())
        if route == "/api/gdtf/search":
            client = gdtf_share()
            try:
                found = client.search(
                    q=query.get("q", ""),
                    man=query.get("man", ""),
                    footprint=int(query["footprint"]) if query.get("footprint") else None,
                    limit=int(query.get("limit", 60)),
                    force=query.get("refresh", "") in ("1", "true", "yes"))
                for r in found.get("results") or []:
                    r["body"] = fixture_kind.describe({
                        "manufacturer": r.get("manufacturer"),
                        "model": r.get("fixture"), "map": []})
                return self._json(found)
            except gdtfshare.GdtfShareError as exc:
                return self._json({"error": exc.message, "code": exc.code,
                                   **client.status()}, 200)
        self._json({"error": "unknown endpoint"}, 404)


def main() -> None:
    # Seeding generic profiles is opt-in (FIXTURE_SEED_BUILTINS): a guessed
    # channel map can mis-address a real light.
    if config.FIXTURE_SEED_BUILTINS:
        fixtures.seed_generics(config.DB_PATH)
        profiles.install(config.DB_PATH)    # data-driven fixture definitions
    # After an update: re-read installed GDTF files if the importer learned
    # something new, so nobody has to download their fixtures again.
    try:
        done = fixtures.refresh_imports(config.DB_PATH,
                                        [config.GDTF_SHARE_CACHE, config.INBOX])
        if done["refreshed"]:
            print(f"* fixtures: re-read {done['refreshed']} profile(s) with the new importer")
        for err in done["errors"]:
            print(f"* fixtures: {err}")
    except Exception as exc:                # noqa: BLE001 - never block boot
        print(f"* fixtures: refresh skipped ({exc})")
    engine_mod.ENGINE = engine_mod.Engine(
        db_path=config.DB_PATH, dry_run=config.CONSOLE_DRY_RUN,
        autosave_path=(config.DATA / "autosave.json")
        if config.CONSOLE_AUTOSAVE else None,
        restore=config.CONSOLE_AUTORESTORE)
    # the restored rig picks up anything the channel naming learned since
    # (a role that was wrong before an update is right after it)
    try:
        engine_mod.ENGINE.remap_heads()
    except Exception as exc:                # noqa: BLE001 - never block boot
        print(f"* fixtures: re-map skipped ({exc})")
    # M6 peripherals: both degrade to a status entry when unavailable,
    # so a missing MIDI device or a busy UDP port never blocks boot.
    if config.DMX_INPUT:
        dmxin.start_from_config(config)
    midi.start_from_config(engine_mod.ENGINE, config)
    engine_mod.ENGINE.ensure_venue("club")      # a fresh desk opens in a club

    try:
        server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    except OSError as exc:
        print(f"* Cannot open port {config.PORT}: {exc}")
        print("  Is another Jarvis already running? Close it (or change PORT in .env) and retry.")
        sys.exit(1)
    server.daemon_threads = True
    if config.requires_token():
        if not config.CONSOLE_TOKEN:
            print(f"* WARNING: bound to {config.HOST} (all interfaces) with no "
                  f"CONSOLE_TOKEN -")
            print("  /api/console can put real DMX on the wire, so anyone on "
                  "this network")
            print("  can drive the rig.  Set CONSOLE_TOKEN in .env, or bind "
                  "to 127.0.0.1.")
        else:
            print(f"* control API token required (CONSOLE_TOKEN set, "
                  f"{config.HOST} is not loopback)")
    dmx_mode = "ARMED" if not config.CONSOLE_DRY_RUN else "DRY RUN"
    brain = f"LLM {config.LLM_MODEL}" if config.LLM_API_KEY else "OFFLINE (no LLM key)"
    print(f"* {config.APP_NAME} -> http://{config.HOST}:{config.PORT}")
    print(f"* fixtures: {fixtures.count(config.DB_PATH)} | brain: {brain}")
    print(f"* console: {config.DMX_TRANSPORT} {config.DMX_HOST}:{config.DMX_PORT} "
          f"@ {config.DMX_HZ} Hz [{dmx_mode}]")
    if config.DMX_INPUT:
        dmx_status = dmxin.snapshot()
        dmx_err = dmx_status.get("errors") or {}
        print("* dmx input: art-net udp/"
              f"{config.DMX_INPUT_ARTNET_PORT} + sacn udp/"
              f"{config.DMX_INPUT_SACN_PORT} -> "
              + ("; ".join(f"{k}: {v}" for k, v in dmx_err.items())
                 or "listening"))
    midi_info = midi.status()
    if midi_info.get("enabled"):
        print("* midi: " + (f"open ({midi_info.get('device')})"
                            if midi_info.get("open")
                            else (midi_info.get("error") or "idle")))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n* bye")
    finally:
        midi.stop()                    # join the reader thread first
        dmxin.stop()                   # then the input listener threads
        if engine_mod.ENGINE is not None:
            engine_mod.ENGINE.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
