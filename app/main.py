"""Jarvis desktop server - stdlib only.

Run:  python app/main.py   (or double-click run.bat)
Then: http://localhost:8787
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hmac
from urllib.parse import parse_qs, urlparse

# Works both as `python app/main.py` and `python -m app.main`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import artnet, config, console_ai, dmxin, fixtures, gdtf_geom  # noqa: E402
from app import fixture_kind  # noqa: E402
from app.engine_support import channel_role  # noqa: E402
from app import midi, profiles                                          # noqa: E402
from app import gdtfshare                                            # noqa: E402
from app import engine as engine_mod                                   # noqa: E402
from tools import import_gdtf                    # noqa: E402

# `agent`, `showread`, `showbuild` and `layouts` went with the assistant
# page.  `console_ai` stays and still pulls in `llm` and `showdesign`, which
# is where the console's own AI lives: the plain-text compiler panel and
# show-from-a-prompt.  Dropping the chat surface did not mean dropping the
# AI, and it is worth being explicit that those two modules are the reason.
# GDTF archives arrive as base64 in the JSON body, and a big rig profile is
# a few MB.  (The comment used to say "photos" - that was the assistant
# page's image attach, which went with it; the limit itself is still for the
# fixture library.)
MAX_BODY = 30 * 1024 * 1024

_MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".woff2": "font/woff2", ".glb": "model/gltf-binary",
    ".txt": "text/plain; charset=utf-8", ".ico": "image/x-icon",
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
        # Every JSON response in the console comes through here, so this is
        # the one that matters.  The §17.27 fix added these headers to the
        # STATIC file path and the test counted call sites rather than
        # checking which methods send a response - so this method, the one
        # carrying the entire API, was left without nosniff, without
        # Referrer-Policy, and without frame-ancestors, and the test stayed
        # green.  A count cannot see a method that was never there.
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
        """A model file's bytes, with the type its extension implies.

        GDTF models are GLB, 3DS, OBJ or STL, and a browser will not guess:
        the Content-Type is derived rather than defaulted.  A GLB served as
        octet-stream still loads; a GLB served as anything else is a
        confusing failure in three different places.
        """
        mime = {
            ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
            ".3ds": "application/octet-stream", ".obj": "text/plain",
            ".stl": "model/stl",
        }.get((ext or "").lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        # Content-addressed by the definition cache, so a given URL's bytes
        # never change: safe to cache hard in the browser and any proxy.
        self.send_header("Cache-Control", "public, max-age=604800")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path, status: int = 200) -> None:
        """Serve a static file, revalidated rather than cached.

        These pages and scripts are the CLIENT half of a versioned API, and
        the two halves ship together.  With no caching headers at all the
        browser applies heuristic freshness and happily keeps running last
        week's viz.js against today's engine - which does not fail loudly,
        it just quietly stops working: new fields the client does not know
        to read are ignored, and a changed contract breaks in ways nobody
        can see from the server logs.  That is not a hypothetical here -
        it is exactly what happened while adding pan/tilt to the look feed,
        and it cost a long debugging detour.

        `no-cache` does NOT mean "do not cache": it means "revalidate
        before every use".  The ETag makes that revalidation a 304 with an
        empty body, so the cost is one conditional request per asset, and
        a client can never be more than one file version behind.
        """
        if not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        # Explicit for the web types: on Windows `mimetypes` reads the
        # registry, which often maps .js to text/plain - and a browser
        # refuses to run an ES module served as text/plain.
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
        """Headers on every response, for a page that can move real fixtures.

        A UI carrying GO LIVE, BLACKOUT, a master fader and cue execution is
        worth more framed inside somebody else's page than an ordinary CRUD
        form is: one click, on real equipment, with the operator watching.
        `frame-ancestors 'none'` denies being embedded at all.

        They live in ONE method called from both the 200 and the 304 path,
        because the first version of this put them on the 304 only - the
        `Cache-Control` line appears twice in `_file` and the edit landed on
        the first one.  So a normal page load carried no security headers
        and only a revalidation did, which is precisely backwards.
        """
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    # -- auth ------------------------------------------------------------
    def _authorised(self) -> bool:
        """Token check for the control API.

        The server binds to loopback by default, so on a single machine
        there is nothing to authenticate.  The moment HOST is set to
        0.0.0.0 (a tablet on the LAN is the obvious reason), /api/console
        can start and stop LIVE DMX - which must not be reachable by
        anyone on the network.  So: on a non-loopback bind a token is
        REQUIRED, and it is also accepted on loopback (harmless, and it
        lets one phone drive the desk).

        `hmac.compare_digest`, not `==`.  An ordinary string comparison
        short-circuits on the first differing byte, so how long it takes
        leaks how much of a guess was right.  That is a real side channel
        against a credential that can drive real lights.

        The previous version of this docstring CLAIMED to be constant time
        while the code underneath it was `==`.  That is worse than no
        comment at all: the comment is what stopped anyone looking, and a
        comment that lies about a security property is a trap rather than
        documentation.

        The token is NOT accepted in the query string.  It used to be, "for
        clients that cannot set headers" - and nothing in this repository
        used it, because the browser sends `X-Jarvis-Token` on every call.
        A credential that can move real fixtures does not belong in a URL:
        those end up in browser history, in `Referer` headers, in
        reverse-proxy and access logs, in links people paste to each other,
        and in screenshots.  No real client cannot set a header either; a
        shell can: `curl -H`.
        """
        token = config.CONSOLE_TOKEN
        if not token:
            return not config.requires_token()
        return hmac.compare_digest(self.headers.get("X-Jarvis-Token") or "", token)

    # THE AUTH BOUNDARY, INVERTED.
    #
    # This used to be a prefix test:
    #
    #     if route.startswith(("/api/console", "/api/gdtf")): gate it
    #
    # which is safe for the routes it names and unsafe for everything it does
    # not.  Four POST routes mutate the persistent fixture library - create,
    # channel, range, import - and none of them begin with either prefix, so
    # on a network-bound server they ran with NO token.
    #
    # Measured, not reasoned about: with HOST=0.0.0.0 and CONSOLE_TOKEN set,
    # an unauthenticated `POST /api/fixtures/create` returned **200** and
    # wrote a profile into the database.  That is not an information leak.
    # The fixture library decides each head's channel-to-role map, so it
    # decides what the engine does with DMX - anyone who could reach the port
    # could change what the rig would be told.  The other two returned 400,
    # which is a VALIDATION error: their handlers had already run.
    #
    # The fix is not a longer prefix list - it is the safe default.  The
    # public routes are now ENUMERATED and everything else under /api/ needs
    # the token, so a new endpoint is private until somebody deliberately
    # makes it public.  That is the only direction that fails safe.
    #
    # Deliberately still public, because the console's shell has to render
    # before it has a token and a 401 on a GET would leave the operator
    # staring at a blank page with no way to enter one:
    #   GET /api/status    - the pills: brain, fixtures, dmx, dry run
    #   GET /api/fixtures  - the fixture search box
    # Both are read-only and neither reveals a credential.  Every POST, every
    # console route, every GDTF route, and every endpoint that does not exist
    # yet are authenticated.
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

        # THE CONSOLE IS THE APP.  There is no second front end and no
        # assistant page to choose between: `/` IS the console.
        #
        # It used to serve `index.html`, a chat page that linked across to
        # `/console.html`.  Two front ends for one desk meant the operator
        # had to work out which one they were in, and every feature had to
        # be built twice or deliberately left on one of them.  The AI stayed
        # - it is a panel inside the console, on the same allowlist, reaching
        # the same engine - and the page around it went.
        if route in ("/", "/index.html"):
            return self._file(config.WEB / "console.html")
        if route == "/" + config.APP_LOGO:
            logo = config.logo_path()
            return self._file(logo) if logo else self._json({"error": "not found"}, 404)
        if route.startswith("/api/"):
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            refused = self._same_origin("GET")
            if refused:
                return self._json({"error": refused}, 403)
            # Public-by-default would be wrong: see PUBLIC_API_GET.  An
            # endpoint is public because it is ON that list, not because it
            # failed to match a prefix.
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
        # EVERY POST is authenticated.  There is nothing in this application
        # a browser should be able to change without a token, and the four
        # fixture-library mutations that used to slip through the old prefix
        # test are exactly why there is no public-write list at all.
        if self._needs_auth(route, "POST") and not self._authorised():
            return self._deny()
        try:
            body = self._read_body()
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)

        try:
            # GONE with the assistant page: /api/chat, /api/session/reset
            # (the conversation), /api/layout (the rig studio), /api/show and
            # /api/show/program (the show audit and the show builder).
            #
            # The AI is NOT gone - it is `/api/console/ai` and
            # `/api/console/generate`, inside the console, on the same
            # allowlist.  What went was the chat SURFACE around it, and five
            # features that existed only there.
            #
            # Each of those was a way to reach the engine that bypassed the
            # console's own undo, lock and dry-run story, which is the actual
            # reason they went rather than the tidiness of it.  `showbuild`
            # for instance wrote a patch straight through; a colour the desk
            # had applied could not be undone from the desk that applied it.
            if route == "/api/fixtures/channel":
                # Rename one channel of a mode.  This is the only way to
                # give a `raw` channel a control, and it is a fixture
                # LIBRARY change, so the patch is re-resolved after it -
                # otherwise the operator fixes a profile and the heads
                # already patched with it stay un-d drivable until a
                # restart, which reads as the fix having done nothing.
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
                # The other half of the label editor.  Naming a channel
                # "Tilt" says what it is; this says how far it goes, which
                # is what lets the desk offer "90 degrees" instead of an
                # abstract number.  Re-maps for the same reason the label
                # edit does: a range is useless on a stale map.
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
                # Credentials arrive over the loopback connection and are
                # kept in this process's memory only - never written to
                # .env, never echoed back.  A wrong password is a normal
                # answer here, not a 500: the operator simply types it
                # again, and a stack trace would be noise.
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
            if route == "/api/gdtf/download":
                client = gdtf_share()
                try:
                    done = client.download(body.get("rid"))
                except gdtfshare.GdtfShareError as exc:
                    return self._json({"error": exc.message, "code": exc.code,
                                       **client.status()}, 200)
                # The library just changed, so every cached view of it must
                # be dropped - including the engine's own copy, or the
                # add-heads picker would keep offering the old modes.
                # _FIXTURE_CACHE is module-level in app/engine.py, not an
                # attribute of the Engine instance.
                fixtures.invalidate_cache()
                engine_mod._FIXTURE_CACHE.clear()
                # The geometry behind those profiles moved with them,
                # so the 3D twin's built definitions are stale too.
                # Keyed by file name, so a re-import of a NEWER file
                # under the same name would otherwise keep serving the
                # old node tree.
                gdtf_geom.clear_manifest_cache()
                return self._json({**done, **client.status()})
            if route in ("/api/console", "/api/console/patch",
                         "/api/console/import_show",
                         "/api/console/save", "/api/console/load",
                         "/api/console/scan", "/api/console/ai",
                         "/api/console/generate", "/api/console/midi",
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
            found = artnet.scan(
                timeout=_scan_num("timeout", 1.5),
                port=scan_port,
                net=config.DMX_NET,
                sweep_subnets=body.get("sweep", True) is not False,
                sweep_timeout=_scan_num("sweep_timeout", 1.5))
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
                # Apply a plan the operator has already previewed - exactly
                # those steps, re-validated, never a second model call that
                # could answer differently.
                result = {**console_ai._validate(
                    {"reply": body.get("reply") or "OK.",
                     "steps": body["steps"]}), "source": "preview"}
            else:
                result = console_ai.plan(str(body.get("message", "")),
                                         offline=bool(body.get("offline")))
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
        if route == "/api/console/generate":
            # Brief -> 2-3 concepts; the engine is only touched when the
            # operator confirms via /api/console/import_show.
            result = console_ai.generate(
                str(body.get("prompt", "")),
                variant=int(body.get("variant", 0) or 0),
                offline=bool(body.get("offline")))
            return self._console_result(eng, result)

    def _api_get(self, route: str, query: dict) -> None:
        if route == "/api/status":
            status = config.status()
            status["fixtures"] = fixtures.count(config.DB_PATH)
            status["actions"] = sorted(engine_mod.ACTIONS)
            # `session`, `layouts` and `show` are gone with the assistant
            # page.  They were the conversation's memory, the rig studio's
            # session and the show scanner's state - none of which the
            # console has, and all of which would have been dead weight in a
            # status poll it renders once at start-up.
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
            # The DMX truth for the selection: label, role, and the byte
            # that will actually be sent.  On demand rather than on the
            # lite feed - it is a debugging surface, not live state, and
            # 200 heads x 20 channels does not belong in a 20 Hz poll.
            heads = None
            if query.get("heads"):
                try:
                    heads = [int(x) for x in query["heads"].split(",") if x.strip()]
                except ValueError:
                    return self._json({"error": "heads must be a list of numbers"},
                                      400)
            return self._json(self._engine().channel_report(heads))
        if route == "/api/fixtures/profile":
            # The full record for the fixture editor: every mode, its
            # footprint, and each channel's label beside the role that
            # label currently resolves to.  A channel reading `raw` is the
            # one with no control behind it, and it was invisible until the
            # channel sheet made it visible.
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
            # The programmer's missing half: the panel offered an
            # intensity fader, a colour swatch and a free-text box in
            # which you had to already know the role was spelled
            # `gobo_rot`.  A console shows you what the selected fixtures
            # can do and lets you change one value without touching the
            # others.
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
        if route == "/api/console/look":
            # Per-tick LIGHT feed for the visualiser (see §13 fades).  The
            # lite feed above is structure and only ships head looks when
            # the patch changes; a cue fade has to animate, so the light
            # comes from here at 20 Hz.  `since` is only a hint: an
            # unknown or skipped sequence returns a full snapshot.
            try:
                since = int(query.get("since", ""))
            except ValueError:
                since = None
            return self._json(self._engine().look_feed(since))
        if route == "/api/console/models":
            # The 3D twin's manifest: one entry per definition in the patch,
            # shared by every head of that type.  Deliberately NOT per head -
            # a 200-head rig of four types sends four definitions and the
            # browser instantiates them.  Sending 200 copies of the same
            # node tree is the mistake that makes a manifest endpoint
            # unusable on exactly the rigs that need it most.
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

        if route == "/api/console/model":
            # ONE model file's bytes, fetched once per definition by the
            # browser and then cached there - so this is not on the hot path
            # and is deliberately NOT part of the 20 Hz look feed.
            did = str(query.get("id", "")).strip()
            name = str(query.get("name", "")).strip()
            if not did or not name:
                return self._json({"error": "id and name are required"}, 400)
            built = gdtf_geom.manifest_for(
                {"source": did + ".gdtf", "manufacturer": "", "model": did,
                 "mode": "", "map": ["pan", "tilt"]},
                config.GDTF_SHARE_CACHE, config.DATA / "gdtf_models")
            # Reverse lookup on the archive-relative name, NOT on its stem.
            # The index is keyed by the `<Model Name>` a node refers to, and
            # for a real library file the two differ:
            #   node says        Model="DOT"
            #   <Models> says     Name="DOT" File="CSVMdot"
            #   archive holds     models/3ds/CSVMdot.3ds
            # so `Path(name).stem` is "CSVMdot" and the lookup missed - every
            # model of that fixture 404'd, six of them, with nothing in the
            # server log because a 404 is a correct answer to a question
            # asked wrongly.
            #
            # Comparing against the extraction's own list is also the whole
            # of the path safety here: `name` came from a query string, and
            # an exact match against what we extracted admits no traversal,
            # no absolute path and no sibling file.
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
            rows = fixtures.search(config.DB_PATH, query.get("q", ""))
            for r in rows:
                # What the light physically is, so the picker can show the
                # 3D model before anything is patched.
                mode = (r.get("modes") or [{}])[0]
                r["body"] = fixture_kind.describe({
                    "manufacturer": r.get("manufacturer"), "model": r.get("model"),
                    "mode": mode.get("name", ""),
                    "map": [channel_role(c) for c in mode.get("channels") or []]})
            return self._json({"results": rows})
        if route == "/api/gdtf/status":
            # Never raises, and never pretends an empty catalogue means
            # "no fixtures published" - the UI has to be able to tell
            # "not signed in" from "site unreachable" from "genuinely
            # nothing matches", and only a real status can do that.
            return self._json(gdtf_share().status())
        if route == "/api/gdtf/search":
            client = gdtf_share()
            try:
                return self._json(client.search(
                    q=query.get("q", ""),
                    man=query.get("man", ""),
                    footprint=int(query["footprint"]) if query.get("footprint") else None,
                    limit=int(query.get("limit", 60)),
                    force=query.get("refresh", "") in ("1", "true", "yes")))
            except gdtfshare.GdtfShareError as exc:
                return self._json({"error": exc.message, "code": exc.code,
                                   **client.status()}, 200)
        self._json({"error": "unknown endpoint"}, 404)


def main() -> None:
    # Seeding is opt-in.  With FIXTURE_SEED_BUILTINS off, the fixture
    # library holds only what the GDTF Share brought in - a generic's
    # channel map is a guess, and a wrong guess mis-addresses a real
    # light silently.  The trade-off is that the add-heads list is empty
    # until something has been fetched; existing patched heads are
    # unaffected either way, because a head carries its own channel map.
    if config.FIXTURE_SEED_BUILTINS:
        fixtures.seed_generics(config.DB_PATH)
        profiles.install(config.DB_PATH)    # data-driven fixture definitions
    engine_mod.ENGINE = engine_mod.Engine(
        db_path=config.DB_PATH, dry_run=config.CONSOLE_DRY_RUN,
        autosave_path=(config.DATA / "autosave.json")
        if config.CONSOLE_AUTOSAVE else None,
        restore=config.CONSOLE_AUTORESTORE)
    # M6 peripherals: both degrade to a status entry when unavailable,
    # so a missing MIDI device or a busy UDP port never blocks boot.
    if config.DMX_INPUT:
        dmxin.start_from_config(config)
    midi.start_from_config(engine_mod.ENGINE, config)

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
