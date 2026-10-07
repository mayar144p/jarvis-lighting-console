"""GDTF Share client - pull fixture profiles straight from gdtf-share.com.

The GDTF Share is the industry fixture database.  Its REST API needs a
(user, password) login that hands back a session cookie valid for two
hours; with that cookie you can list every published revision and
download any one of them.  That turns "what about other brands" from a
hunt for .gdtf files into a search box.

Stdlib only - urllib, no requests.  Two deliberate design choices:

  * **Cookies are handled here, not by http.cookiejar.**  Cookiejar is
    welded to urllib's opener, which would make the whole flow
    untestable: a suite that cannot reach the network must still be able
    to exercise login, expiry, refresh and download.  Owning a plain
    {name: value} dict and sending/parsing the header ourselves keeps
    every step reachable with a fake transport.

  * **The transport is injectable.**  `transport(method, url, body,
    headers)` -> `(status, headers, bytes)`.  Tests pass a fake, so the
    suite never touches the network and never needs an account.

Credentials are never sent to the browser and never written to disk by
this module.  The session cookie IS persisted (it is a 2-hour token, not
a password) so a server restart does not force a re-login.  Passwords
come from .env when you want unattended use, or are POSTed to
/api/gdtf/login and held in memory only.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from app import fixtures

BASE = "https://gdtf-share.com/apis/public"
LOGIN_URL = f"{BASE}/login.php"
LIST_URL = f"{BASE}/getList.php"
DOWNLOAD_URL = f"{BASE}/downloadFile.php"

# The list is the whole database in one document - thousands of entries,
# megabytes.  Fetching it per search would be absurd, so it is cached and
# searched locally.
LIST_TTL_S = 24 * 60 * 60
DEFAULT_TIMEOUT = 20.0
PAGE = 400          # fixtures.get() page size when we read the DB back


class GdtfShareError(Exception):
    """Every failure carries a stable `code` the UI can branch on.

    A bare HTTP error is not enough here: "your session expired" and "the
    site is down" need very different operator responses, and neither may
    be reported as an empty fixture list - an empty list looks like "this
    brand has no profiles", which is a lie.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _default_transport(method: str, url: str, *, body: bytes | None = None,
                       headers: dict | None = None, timeout: float = 20.0):
    req = urllib.request.Request(url, data=body, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        # A 4xx/5xx still has a body, and for this API the body is where
        # the actual reason lives ("Unauthorized.", "File does not
        # exist.").  Reading it beats inventing a message.
        try:
            payload = exc.read()
        except Exception:                                    # noqa: BLE001
            payload = b""
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, payload
    except urllib.error.URLError as exc:
        raise GdtfShareError("network", f"cannot reach gdtf-share.com: {exc.reason}") from exc
    except TimeoutError as exc:
        raise GdtfShareError("network", "gdtf-share.com timed out") from exc


def _as_json(payload: bytes) -> dict:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GdtfShareError(
            "bad_response", f"gdtf-share.com sent something that is not JSON: {exc}") from exc



def _int(value, default: int = 0) -> int:
    """The Share sends numbers as strings more often than its spec says."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _rating(entry: dict) -> float:
    """`rating` is documented as a float and arrives as a string ("4.1").

    Sorting on it unconverted raises TypeError, which turned a working
    search into a 500 - and the fake transport in the suite returned a
    real float, so only a live call could find it.
    """
    try:
        return float(str(entry.get("rating")).strip())
    except (TypeError, ValueError):
        return 0.0


class GdtfShare:
    """Client for the three public functions: login, getList, downloadFile."""

    def __init__(self, db_path: Path, cache_dir: Path, user: str = "",
                 password: str = "", transport=None, now=None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self.db_path = Path(db_path)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user = (user or "").strip()
        self._password = password or ""
        self._transport = transport or _default_transport
        self._now = now or time.time
        self._timeout = timeout
        self.cookies: dict[str, str] = {}
        self.last_error: str = ""
        self.last_error_code: str = ""
        self._load_session()

    # -- paths ------------------------------------------------------------
    @property
    def _session_file(self) -> Path:
        return self.cache_dir / "session.txt"

    @property
    def _list_file(self) -> Path:
        return self.cache_dir / "list.json"

    # -- session persistence ---------------------------------------------
    # The cookie is a 2-hour token, not a credential the user retypes.  A
    # restart inside that window should not force a login.
    def _load_session(self) -> None:
        if not self._session_file.is_file():
            return
        for line in self._session_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                name, _, value = line.partition("=")
                self.cookies[name.strip()] = value.strip()

    def _save_session(self) -> None:
        self._session_file.write_text(
            "".join(f"{k}={v}\n" for k, v in self.cookies.items()),
            encoding="utf-8")

    def clear_session(self) -> None:
        self.cookies.clear()
        try:
            self._session_file.unlink()
        except FileNotFoundError:
            pass

    # -- transport --------------------------------------------------------
    def _request(self, method: str, url: str, *, body: bytes | None = None,
                 extra: dict | None = None, authed: bool = True) -> tuple[int, dict, bytes]:
        headers = {"User-Agent": "jarvis-console/1.0",
                   "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if authed and self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        headers.update(extra or {})

        status, resp_headers, payload = self._transport(
            method, url, body=body, headers=headers, timeout=self._timeout)

        # The API sets the session cookie on the login response; keep it.
        raw_cookie = ""
        for key, value in resp_headers.items():
            if key.lower() == "set-cookie":
                raw_cookie = value
        if raw_cookie:
            first = raw_cookie.split(";")[0]
            if "=" in first:
                name, _, value = first.partition("=")
                self.cookies[name.strip()] = value.strip()
                self._save_session()

        if status in (401, 403):
            # The cookie is gone or was never valid.  Drop it so the next
            # status() does not keep claiming a session.
            self.clear_session()
            raise GdtfShareError(
                "unauthorized",
                "GDTF Share session expired or was rejected - log in again")
        if status >= 500:
            raise GdtfShareError(
                "server", f"GDTF Share is unavailable (HTTP {status})")
        return status, resp_headers, payload

    def _fail(self, exc: Exception) -> GdtfShareError:
        if isinstance(exc, GdtfShareError):
            self.last_error_code, self.last_error = exc.code, exc.message
            return exc
        self.last_error_code, self.last_error = "error", str(exc)
        return GdtfShareError("error", str(exc))

    # -- credentials ------------------------------------------------------
    def set_credentials(self, user: str, password: str) -> None:
        """Hold an account in memory for this process.

        Deliberately not persisted: the password is a credential, and the
        session cookie (which IS persisted) is what removes the need to
        retype it.  Rejected credentials clear the cookie so a failed
        sign-in cannot inherit a previous, still-unexpired session.
        """
        self.user = (user or "").strip()
        self._password = password or ""
        if not (self.user and self._password):
            self.clear_session()

    # -- 1. login ---------------------------------------------------------
    def login(self) -> dict:
        """POST user+password.  On success the session cookie is kept."""
        if not self.user or not self._password:
            raise GdtfShareError(
                "no_credentials",
                "no GDTF Share account configured - set GDTF_SHARE_USER and "
                "GDTF_SHARE_PASSWORD, or sign in through the console")
        try:
            status, _, payload = self._request(
                "POST", LOGIN_URL,
                body=json.dumps({"user": self.user,
                                 "password": self._password}).encode("utf-8"),
                authed=False)
            data = _as_json(payload)
        except GdtfShareError as exc:
            raise self._fail(exc) from exc
        if not data.get("result"):
            # The API answers a bad login with HTTP 400 AND result:false,
            # so this must branch on `result`, not on the status code -
            # keying off 200 made a wrong password report as "the API
            # contract changed", which is both alarming and wrong.
            raise self._fail(GdtfShareError(
                "unauthorized",
                str(data.get("error") or "GDTF Share rejected those credentials")))
        if not self.cookies:
            raise self._fail(GdtfShareError(
                "bad_response", "GDTF Share accepted the login but sent no "
                                "session cookie - the API contract changed"))
        self.last_error = self.last_error_code = ""
        return {"ok": True, "user": self.user,
                "summary": f"signed in to GDTF Share as {self.user}"}

    def logout(self) -> dict:
        self.clear_session()
        self.last_error = self.last_error_code = ""
        return {"ok": True, "summary": "signed out of GDTF Share"}

    # -- 2. get list ------------------------------------------------------
    def fetch_list(self, force: bool = False) -> dict:
        """The revision catalogue, cached on disk and searched locally."""
        if not force and self._list_file.is_file():
            age = self._now() - self._list_file.stat().st_mtime
            if age < LIST_TTL_S:
                try:
                    cached = json.loads(self._list_file.read_text(encoding="utf-8"))
                    if isinstance(cached.get("list"), list) and cached["list"]:
                        cached["cached"] = True
                        cached["age_s"] = int(age)
                        return cached
                except (OSError, json.JSONDecodeError):
                    pass          # a corrupt cache must not be fatal
        if not self.cookies:
            # Credentials in .env mean the operator never sees a login
            # prompt, so the first search after a restart has to sign in
            # for them.  Without this the call would go out unauthenticated
            # and come back 401 - an expired-session message for what is
            # actually a cold start.
            if self.user and self._password:
                self.login()
            else:
                raise GdtfShareError(
                    "no_session", "not signed in to GDTF Share")
        try:
            status, _, payload = self._request("GET", LIST_URL)
            data = _as_json(payload)
        except GdtfShareError as exc:
            raise self._fail(exc) from exc
        if not data.get("result"):
            raise self._fail(GdtfShareError(
                "unauthorized", str(data.get("error") or "list refused")))
        entries = data.get("list") or []
        if not entries:
            raise self._fail(GdtfShareError(
                "bad_response", "GDTF Share returned an empty catalogue"))
        try:
            self._list_file.write_text(
                json.dumps({"timestamp": data.get("timestamp"), "list": entries}),
                encoding="utf-8")
        except OSError:
            pass              # a cache we cannot write is still usable
        self.last_error = self.last_error_code = ""
        return {"timestamp": data.get("timestamp"), "list": entries,
                "cached": False, "age_s": 0}

    # -- search over the cached catalogue --------------------------------
    @staticmethod
    def _squash(text: str) -> str:
        """Lower-cased with spaces and punctuation dropped.

        The catalogue is inconsistent about spacing: Chauvet publishes
        "Slim Par T12 USB" and "SlimPAR Pro H USB" side by side.  An
        operator searching "slimpar t12" - the obvious thing to type, and
        how the fixture is actually spoken - matched NEITHER under plain
        substring matching, so a fixture that exists looked unpublished.
        """
        return re.sub(r"[^a-z0-9]+", "", str(text).lower())

    @classmethod
    def _score(cls, entry: dict, needle: str, man: str) -> int | None:
        fixture = str(entry.get("fixture", ""))
        maker = str(entry.get("manufacturer", ""))
        low_f, low_m = fixture.lower(), maker.lower()
        if man:
            want = man.strip().lower()
            if not low_m.startswith(want):
                return None
        if not needle:
            return 0
        if low_m == needle or low_f == needle:
            return 0
        if low_m.startswith(needle):
            return 1
        if low_f.startswith(needle):
            return 2
        if needle in low_m:
            return 3
        if needle in low_f:
            return 4
        # Spacing-insensitive rescue, ranked below every exact hit so a
        # precisely-typed query still wins.  This only recovers matches
        # that would otherwise be silently missed; it never reorders the
        # ones already found.
        tight = cls._squash(needle)
        if tight and (tight in cls._squash(maker) or tight in cls._squash(fixture)):
            return 5
        # brand AND model typed together, extra describing words, a typo:
        # the shared forgiving matcher, ranked below every hit above
        from . import searchmatch
        hit = searchmatch.score(needle, maker, fixture)
        if hit is not None:
            return 6 + hit[0] * 3 + min(2, hit[1] + hit[2])
        return None

    def search(self, q: str = "", man: str = "", footprint: int | None = None,
               limit: int = 60, force: bool = False) -> dict:
        """Search the cached catalogue.  No network unless the cache is stale."""
        catalogue = self.fetch_list(force=force)
        needle = (q or "").strip().lower()
        hits = []
        for entry in catalogue["list"]:
            score = self._score(entry, needle, man or "")
            if score is None:
                continue
            modes = entry.get("modes") or []
            if footprint:
                if not any(int(m.get("dmxfootprint") or 0) == footprint
                           for m in modes):
                    continue
            hits.append((score, entry, modes))
        # Best match first, then best rated, then alphabetical - a stable
        # order means the operator's second search is not a reshuffle.
        hits.sort(key=lambda t: (t[0], -_rating(t[1]),
                                 str(t[1].get("fixture", "")).lower()))
        trimmed = hits[:max(1, int(limit or 60))]
        return {
            "total": len(hits),
            "count": len(trimmed),
            "cached": catalogue.get("cached", False),
            "age_s": catalogue.get("age_s", 0),
            "results": [{
                "rid": _int(e.get("rid")) or None,
                "fixture": e.get("fixture"),
                "manufacturer": e.get("manufacturer"),
                "revision": e.get("revision"),
                "rating": _rating(e) or None,
                "uploader": e.get("uploader"),
                "filesize": e.get("filesize"),
                "modes": [{"name": m.get("name"),
                           "dmxfootprint": _int(m.get("dmxfootprint"))}
                          for m in modes],
            } for _s, e, modes in trimmed],
        }

    # -- 3. download ------------------------------------------------------
    def _fetch(self, rid) -> tuple[int, bytes]:
        """One revision's file from the Share (signing in again once if the
        session ran out), or GdtfShareError - never a JSON error page."""
        try:
            rid = int(rid)
        except (TypeError, ValueError) as exc:
            raise GdtfShareError("bad_request", "rid must be a number") from exc
        if not self.cookies:
            if self.user and self._password:
                self.login()
            else:
                raise GdtfShareError("no_session", "not signed in to GDTF Share")
        try:
            try:
                _status, headers, payload = self._request(
                    "GET", f"{DOWNLOAD_URL}?rid={rid}")
            except GdtfShareError as exc:
                # An expired session with an account at hand: sign in
                # again and retry once, instead of failing until restart.
                if exc.code != "unauthorized" or not (self.user and self._password):
                    raise
                self.login()
                _status, headers, payload = self._request(
                    "GET", f"{DOWNLOAD_URL}?rid={rid}")
        except GdtfShareError as exc:
            raise self._fail(exc) from exc

        # A failed download comes back as JSON, not as a file.  Checking
        # the body beats trusting the status code, and importing a JSON
        # error page as a fixture would be a genuinely baffling bug.
        ctype = str(headers.get("content-type", "")).lower()
        head = payload.lstrip()[:1]
        if "json" in ctype or head in (b"{", b"["):
            detail = ""
            try:
                detail = str(_as_json(payload).get("error") or "")
            except GdtfShareError:
                detail = ""
            raise self._fail(GdtfShareError(
                "not_found",
                detail or f"GDTF Share did not return a file for revision {rid}"))
        if not payload:
            raise self._fail(GdtfShareError(
                "bad_response", f"revision {rid} downloaded as an empty file"))

        return rid, payload

    def download(self, rid: int) -> dict:
        """Fetch one revision and install it into the fixture database.

        Re-downloading the same rid is a no-op (the file name carries the
        rid).  Downloading a *newer* revision of a fixture already in the
        library replaces it, so the picker never shows the same model
        twice with stale modes.
        """
        rid, payload = self._fetch(rid)
        target = self.cache_dir / f"rev{rid}.gdtf"
        try:
            target.write_bytes(payload)
            parsed = fixtures.parse_gdtf(target)
        except (OSError, ValueError) as exc:
            raise self._fail(GdtfShareError(
                "bad_file", f"revision {rid} is not a readable GDTF: {exc}")) from exc
        if not parsed:
            raise self._fail(GdtfShareError(
                "bad_file", f"revision {rid} describes no fixture"))

        item = parsed[0]
        maker = item["manufacturer"]
        model = item["model"]
        # One entry per model.  Matching is loose on purpose: the Share
        # writes "Chauvet" where the curated profile library writes
        # "CHAUVET DJ", and an exact match would leave the same light in
        # the picker twice under two names.
        prior = fixtures.model_sources(self.db_path, maker, model, loose=True)
        refreshed = target.name in prior
        superseded = fixtures.remove_model(self.db_path, maker, model,
                                           loose=True) if prior else []
        try:
            fixtures.import_file(self.db_path, target)
        except (OSError, ValueError) as exc:
            raise self._fail(GdtfShareError(
                "import_failed", f"could not install {maker} {model}: {exc}")) from exc
        self.last_error = self.last_error_code = ""
        verb = "refreshed" if refreshed else ("replaced" if superseded else "added")
        note = f" (superseded {', '.join(superseded)})" if superseded and not refreshed else ""
        return {
            "ok": True, "rid": rid, "manufacturer": maker, "model": model,
            "replaced": bool(superseded) and not refreshed,
            "refreshed": refreshed,
            "superseded": superseded,
            "modes": [{"name": m["name"], "channel_count": m["channel_count"]}
                      for m in item["modes"]],
            "path": str(target),
            "summary": f"{verb} {maker} {model} "
                       f"({len(item['modes'])} mode(s)){note}",
        }

    # -- 4. a real body for the 3D, the profile left alone ---------------
    # A light patched from the QLC+ / Open Fixture libraries has no 3D
    # model; the same light on the Share often has the maker's own.  Its
    # file is kept for the 3D only (bodies.json: model -> file): the
    # channels, modes and everything programmed stay the profile's own.
    @property
    def _bodies_file(self) -> Path:
        return self.cache_dir / "bodies.json"

    def _bodies(self) -> dict:
        try:
            return json.loads(self._bodies_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @classmethod
    def _body_key(cls, manufacturer: str, model: str) -> str:
        return cls._squash(manufacturer) + "/" + cls._squash(model)

    def body_file(self, manufacturer: str, model: str) -> str | None:
        """The .gdtf (in the cache) whose 3D model this light uses, or None."""
        got = self._bodies().get(self._body_key(manufacturer, model)) or {}
        name = got.get("file") or ""
        return name if name and (self.cache_dir / name).is_file() else None

    def fetch_body(self, manufacturer: str, model: str) -> dict:
        """Find this light on the Share and keep its file for the 3D, if it
        carries a 3D model.  {ok, file?, fixture?, reason?}"""
        from zipfile import BadZipFile, ZipFile

        from .gdtf_geom import model_members
        want_m, want = self._squash(manufacturer), self._squash(model)
        found = self.search(q=model, limit=40).get("results") or []
        # the same model by the same maker (the Share and the libraries spell
        # makers differently: "Clay Paky" / "Claypaky", "Chauvet" / "CHAUVET DJ")
        same = [r for r in found if self._squash(r.get("fixture")) == want
                and (self._squash(r.get("manufacturer"))[:4] == want_m[:4] or not want_m)]
        if not same:
            return {"ok": False, "reason": "not on GDTF Share"}
        best = same[0]
        rid, payload = self._fetch(best["rid"])
        name = f"body-rev{rid}.gdtf"
        try:
            from io import BytesIO
            with ZipFile(BytesIO(payload)) as zf:
                has_models = bool(model_members(zf))
        except BadZipFile:
            return {"ok": False, "reason": "the Share's file is not a GDTF"}
        if not has_models:
            return {"ok": False, "reason": "its GDTF has no 3D model"}
        (self.cache_dir / name).write_bytes(payload)
        bodies = self._bodies()
        bodies[self._body_key(manufacturer, model)] = {"file": name, "rid": rid,
                                                       "fixture": f"{best.get('manufacturer')} {best.get('fixture')}"}
        self._bodies_file.write_text(json.dumps(bodies, indent=1), encoding="utf-8")
        return {"ok": True, "file": name, "fixture": bodies[self._body_key(manufacturer, model)]["fixture"]}

    # -- status -----------------------------------------------------------
    def status(self) -> dict:
        """Never raises.  The UI renders whatever this returns, so it has
        to be honest about every failure rather than defaulting to a
        plausible-looking "no fixtures available"."""
        count = 0
        age = None
        if self._list_file.is_file():
            try:
                cached = json.loads(self._list_file.read_text(encoding="utf-8"))
                count = len(cached.get("list") or [])
                age = max(0, int(self._now() - self._list_file.stat().st_mtime))
            except (OSError, json.JSONDecodeError):
                count = 0
        return {
            "configured": bool(self.user and self._password),
            "user": self.user,
            # "signed in" means we hold a live session, not merely that
            # credentials exist.  With .env credentials the next call
            # signs in on its own, so `configured` is the honest
            # "will work unattended" signal and this is the honest
            # "is authenticated right now" one.
            "signed_in": bool(self.cookies),
            "has_cookie": bool(self.cookies),
            "catalogue": count,
            "catalogue_age_s": age,
            "stale": age is not None and age >= LIST_TTL_S,
            "last_error": self.last_error,
            "last_error_code": self.last_error_code,
        }
