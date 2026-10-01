"""Self-test suites, part 5: web_app, hardening, fixture_kind, gdtf_share, api_auth, gdtf_geometry, fx_library."""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path

from app import fixtures
from tools.selftests.common import (
    ROOT,
    _share_gdtf_bytes,
    _share_login_ok,
    _share_transport,
    _which,
    check,
)

    # A drag must not be a hundred HTTP requests, and a click that changes
    # nothing must not cost an undo step.  `set_colour` is already in
    # UNDO_COALESCE, which is what makes the throttled drag one Ctrl+Z.
    # Wiring a control is not the same as drawing it.  `wirePicker` only
    # attaches listeners, and `renderProgrammer` only repaints when the hex
    # field holds something it can parse - which on a fresh load with a clear
    # programmer it does not.  So the picker sat there as a blank 196px
    # square with every other check in this suite green.  Only reading the
    # pixels found it.

    # Keyboard: a canvas is focusable and silent, so without this the whole
    # control is unreachable without a mouse.
    # The axis decision lives in a pure function the suite can run, NOT
    # inline in the handler where a transposed argument pair is invisible.
    # A NaN does not look like a NaN on the wire: clampInt returns its
    # default of 0, so it becomes a CONCRETE WRONG COLOUR.  That is how the
    # transposed axis turned a white head black instead of throwing.


def test_web_app() -> None:
    """The new frontend: its wiring, measured rather than eyeballed."""
    print("web app (modules, ids, actions, routes, maths, stream)")
    import subprocess as _sp

    from app import engine as eng_mod
    from app import fixture_kind
    web = ROOT / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    mods = sorted((web / "app").glob("*.js")) + sorted((web / "js").rglob("*.js"))
    srcs = {m: m.read_text(encoding="utf-8") for m in mods}

    check("pdf.js is vendored for PDF floor plans",
          (web / "vendor" / "pdfjs" / "pdf.min.mjs").is_file()
          and (web / "vendor" / "pdfjs" / "pdf.worker.min.mjs").is_file(), "")
    check("the move gizmo is vendored",
          (web / "vendor" / "three" / "addons" / "controls" / "TransformControls.js").is_file(), "")
    check("the page maps `three` and its addons to vendored files",
          '"three": "/vendor/three/three.module.js"' in html
          and (web / "vendor" / "three" / "three.module.js").is_file()
          and (web / "vendor" / "three" / "three.core.js").is_file(), "")
    bad = []
    for m, s in srcs.items():
        for spec in re.findall(r'^\s*import\s[^;]*?from\s+"([^"]+)"', s, re.M):
            if spec == "three":
                continue
            if spec.startswith("three/addons/"):
                target = web / "vendor" / "three" / "addons" / spec[len("three/addons/"):]
            elif spec.startswith("/"):
                target = web / spec.lstrip("/")
            else:
                target = (m.parent / spec).resolve()
            if not target.is_file():
                bad.append(f"{m.name}: {spec}")
    check("every module import resolves to a file that ships", not bad, str(bad))

    # Show building: per-cue follow is tri-state (null inherits, 0 waits,
    # seconds auto-run), and the cue list and keys must be able to say all three.
    dlg = (web / "app" / "dialogs.js").read_text(encoding="utf-8")
    keys = (web / "app" / "keys.js").read_text(encoding="utf-8")
    check("the cue list edits follow as inherit / wait / auto",
          'sel.value === "inherit" ? null : sel.value === "wait" ? 0' in dlg
          and 'run("edit_cue", { playback: n, cue: c.n, follow })' in dlg, "")
    check("the cue list draws a fade / hold / follow timeline per cue",
          all(f'"seg-{k}"' in dlg for k in ("fade", "hold", "follow")), "")
    check("a cue can be inserted from the list and the keyboard",
          'run("insert_cue"' in dlg and 'run("insert_cue"' in keys, "")
    check("O overwrites and D deletes the cue the playback is on",
          'low === "o"' in keys and 'low === "d"' in keys
          and 'run("delete_cue"' in keys, "")

    ids = set(re.findall(r'\bid="([^"]+)"', html))
    used = set()
    for s in srcs.values():
        used |= set(re.findall(r'\$\("#([\w-]+)', s))
    missing = sorted(used - ids)
    check("every element the app reaches for exists in the page", not missing,
          str(missing))

    actions = set()
    for s in srcs.values():
        actions |= set(re.findall(r'\brun\("([a-z_]+)"', s))
        actions |= set(re.findall(r'\bact\("([a-z_]+)"', s))
    unknown = sorted(a for a in actions if a not in eng_mod.ACTIONS)
    check("every engine action the UI calls exists", not unknown and actions,
          str(unknown))

    main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    routes = set()
    for s in srcs.values():
        routes |= set(re.findall(r'"(/api/[a-z_/]+)', s))
    missing_routes = sorted(r for r in routes if f'"{r}"' not in main_src)
    check("every API route the UI calls is served", not missing_routes,
          str(missing_routes))

    api = (web / "app" / "api.js").read_text(encoding="utf-8")
    check("the access token lives for this tab only (sessionStorage)",
          "sessionStorage" in api and "localStorage" not in api, "")
    check("and a 401 cannot open a prompt loop",
          "snoozeUntil" in api and "prompting" in api, "")

    models = (web / "js" / "stage" / "models.js").read_text(encoding="utf-8")
    table = models[models.index("const BUILDERS = {"):]
    table = table[:table.index("};")]
    missing_types = [t for t in fixture_kind.TYPES
                     if not re.search(rf"(^|\s){t}[,:]", table, re.M)]
    check("every physical fixture type has a 3D model builder",
          not missing_types, str(missing_types))
    check("the stage never uses three's TDSLoader, which hangs on real "
          "GDTF 3DS files", not any("TDSLoader" in s and "import" in s
                                    for s in srcs.values()
                                    if "TDSLoader" in s.split("//")[0]), "")

    node = _which("node")
    if node is None:
        print("  skip  node not found - the maths below is not run")
        return
    picker = (web / "app" / "picker.js").as_uri()
    script = (
        f'import {{ hsvToRgb, rgbToHsv, hexToRgb, rgbToHex }} from "{picker}";'
        'const out = {cyan: hsvToRgb(180, 1, 1), magenta: hsvToRgb(300, 1, 1),'
        ' red: hsvToRgb(0, 1, 1), half: hsvToRgb(0, 1, 0.5),'
        ' back: rgbToHsv(0, 255, 255), grey: rgbToHsv(128, 128, 128),'
        ' hex: hexToRgb("#ff8000"), short: hexToRgb("f80"), junk: hexToRgb("#zzzzzz"),'
        ' round: rgbToHex(...hsvToRgb(...rgbToHsv(18, 52, 86)))};'
        'console.log(JSON.stringify(out));')
    proc = _sp.run([node, "--input-type=module", "-e", script],
                   capture_output=True, text=True, timeout=30)
    try:
        got = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        got = {}
    check("the picker's maths runs under node", bool(got),
          (proc.stderr or proc.stdout)[:200])
    if got:
        check("hue 180 is cyan and 300 is magenta (catches a swapped R/B)",
              got["cyan"] == [0, 255, 255] and got["magenta"] == [255, 0, 255],
              str(got))
        check("half brightness is a dark red", got["half"] == [128, 0, 0],
              str(got["half"]))
        check("cyan reads back as hue 180, grey as saturation 0",
              abs(got["back"][0] - 180) < 0.01 and got["grey"][1] == 0, str(got))
        check("hex parses long and short, and junk is null",
              got["hex"] == [255, 128, 0] and got["short"] == [255, 136, 0]
              and got["junk"] is None, str(got))
        check("a colour survives a round trip", got["round"] == "#123456",
              got["round"])

    # The stage modules import bare `three`: map it for node with a resolve
    # hook, then run the 3DS scanner on good and hostile input.
    three = (web / "vendor" / "three" / "three.module.js").as_uri()
    hook = ("data:text/javascript," + urllib_quote(
        'export async function resolve(s, c, n) {'
        f' if (s === "three") return {{ url: "{three}", shortCircuit: true }};'
        ' return n(s, c); }'))
    register = ("data:text/javascript," + urllib_quote(
        'import { register } from "node:module";'
        f' register("{hook}");'))
    from tools import _gdtf_fixtures as gf
    cube_v = tuple((x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1))
    cube_f = ((0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
              (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3))
    good = gf.three_ds(cube_v, cube_f, pad=b"\0" * 24)
    hostile = b"MM" + (6).to_bytes(4, "little") + b"\x00" * 64
    parser = (web / "js" / "stage" / "parse3ds.js").as_uri()
    script = (
        f'import {{ parse3DS }} from "{parser}";'
        f'const good = Uint8Array.from({list(good)});'
        f'const bad = Uint8Array.from({list(hostile)});'
        'const g = parse3DS(good.buffer); const b = parse3DS(bad.buffer);'
        'let faces = 0; g.traverse((o) => { if (o.isMesh) faces += o.geometry.index.count / 3; });'
        'console.log(JSON.stringify({faces, meshes: g.children.length, bad: b.children.length}));')
    proc = _sp.run([node, "--import", register, "--input-type=module", "-e", script],
                   capture_output=True, text=True, timeout=30)
    try:
        got = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        got = {}
    check("the 3DS scanner reads a real mesh (12 triangles of a cube)",
          got.get("faces") == 12 and got.get("meshes") == 1,
          str(got) or (proc.stderr or "")[:200])
    check("and returns nothing, quickly, for a hostile file", got.get("bad") == 0,
          str(got))

    # The doctor: findings a tech can act on, with fixes the desk can run.
    import tempfile as _tf

    from app import doctor
    tmpd = Path(_tf.mkdtemp())
    db = tmpd / "doc.db"
    fixtures.seed_generics(db)
    e = eng_mod.Engine(db_path=db, dry_run=True, show_dir=tmpd / "shows")
    rep = doctor.examine(e)
    check("an empty rig is an error the doctor names",
          rep["errors"] == 1 and "Nothing is patched" in rep["findings"][0]["title"],
          json.dumps(rep)[:200])
    e.act("add_heads", query="LED PAR", qty=2, universe=3, address=1)
    rep = doctor.examine(e)
    fixes = [f.get("fix", {}).get("action") for f in rep["findings"]]
    check("skipped universes are found, with the fix the desk can run",
          any("unused" in f["title"] for f in rep["findings"])
          and "auto_patch" in fixes, json.dumps(rep)[:300])
    check("every suggested fix is a real engine action",
          all(a in eng_mod.ACTIONS for a in fixes if a), str(fixes))
    e.act("auto_patch")
    rep = doctor.examine(e)
    check("and once packed, that finding is gone",
          not any("unused" in f["title"] for f in rep["findings"]), "")
    e.shutdown()


def urllib_quote(text: str) -> str:
    from urllib.parse import quote
    return quote(text, safe="")


def test_hardening(tmp: Path) -> None:
    """Regressions for the audit fixes: cross-site writes, bad GETs,
    atomic AI batches, read-only actions and the offline compiler."""
    print("hardening")
    import os as _os
    import subprocess as _subprocess
    import sys as _sys
    import time as _time
    import urllib.error
    import urllib.request

    from app import console_ai
    from app import engine as eng_mod

    # ---- engine: atomic batches and undo hygiene -------------------------
    db = tmp / "hard.db"
    fixtures.seed_generics(db)
    e = eng_mod.Engine(db_path=db, dry_run=True, show_dir=tmp / "hard-shows")
    e.act("add_heads", query="LED PAR", qty=4)
    depth = len(e._undo)
    e.act("fx_available")
    check("a read-only query costs no undo step", len(e._undo) == depth,
          "%d -> %d" % (depth, len(e._undo)))
    res = e.act_batch([
        {"action": "select_all", "params": {}},
        {"action": "set_intensity", "params": {"level": 60}},
        {"action": "set_colour", "params": {"hex": "#00ff00"}}])
    check("a batch runs every step", res["ok"] and res["executed"] == 3,
          json.dumps(res))
    check("and costs exactly one undo step", len(e._undo) == depth + 1,
          str(len(e._undo)))
    e.act("undo")
    check("one undo reverses the whole batch",
          not any(e.programmer.values()) and e.selected == [],
          json.dumps(e.programmer))
    before = json.dumps(e.programmer, sort_keys=True)
    res = e.act_batch([
        {"action": "select_all", "params": {}},
        {"action": "set_intensity", "params": {"level": 80}},
        {"action": "set_attribute", "params": {"attribute": "nonsense",
                                               "value": 3}}])
    check("a failing batch reports the failure", not res["ok"]
          and res.get("rolled_back"), json.dumps(res))
    check("and leaves nothing half-applied",
          json.dumps(e.programmer, sort_keys=True) == before, "")

    # ---- programmer fades ------------------------------------------------
    e.act("select_all")
    e.act("set_intensity", level=100)
    t0 = _time.monotonic()
    e.act("set_intensity", level=0, fade=10)
    mid = e.build_frames(t0 + 5)[1][0]
    end = e.build_frames(t0 + 11)[1][0]
    check("set_intensity fade= really fades", 90 <= mid <= 165 and end == 0,
          "mid %d end %d" % (mid, end))

    # ---- offline compiler ------------------------------------------------
    def steps(text):
        return [(s["target"], s["action"])
                for s in console_ai.plan(text, offline=True)["steps"]]
    check("'pan to 90' aims rather than starting an effect",
          steps("pan to 90") == [("auto", "set_position")],
          str(steps("pan to 90")))
    check("'go red' does not fire a cue", ("auto", "cue_go")
          not in steps("go red"), str(steps("go red")))
    check("'red on 1-4' targets heads 1-4",
          steps("red on 1-4") == [("heads 1-4", "set_colour")],
          str(steps("red on 1-4")))
    check("'movers to 50%' targets the moving heads",
          steps("movers to 50%") == [("type movers", "set_intensity")],
          str(steps("movers to 50%")))
    check("'zoom 40' sets the zoom attribute",
          steps("zoom 40") == [("auto", "set_attribute")],
          str(steps("zoom 40")))
    calls = console_ai.resolve(
        console_ai.plan("heads 1,3 blue", offline=True)["steps"], e)
    check("scattered heads are selected exactly, not refused",
          calls[0] == {"step": 1, "action": "select_heads",
                       "params": {"heads": [1, 3]}}, json.dumps(calls))

    # ---- HTTP: a web page on another site cannot drive the desk ----------
    port = 8973
    env = dict(_os.environ, PORT=str(port), HOST="127.0.0.1",
               CONSOLE_TOKEN="", CONSOLE_DRY_RUN="true",
               FIXTURE_DB=str(tmp / "http.db"),
               CONSOLE_SHOW_DIR=str(tmp / "http-shows"),
               CONSOLE_AUTOSAVE="false", MIDI_ENABLED="false")
    proc = _subprocess.Popen([_sys.executable, _os.path.join("app", "main.py")],
                             env=env, cwd=str(ROOT),
                             stdout=_subprocess.DEVNULL,
                             stderr=_subprocess.DEVNULL)

    def call(path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                     data=data,
                                     method="POST" if body is not None
                                     else "GET")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:
            return 0

    try:
        for _ in range(40):
            if call("/api/status") == 200:
                break
            _time.sleep(0.25)
        blackout = {"action": "blackout", "params": {"state": 1}}
        json_h = {"Content-Type": "application/json"}
        check("a same-origin JSON POST works",
              call("/api/console", blackout, json_h) == 200, "")
        check("a text/plain POST (what a hostile page can send) is refused",
              call("/api/console", blackout,
                   {"Content-Type": "text/plain"}) == 403, "")
        check("a POST naming another Origin is refused",
              call("/api/console", blackout,
                   dict(json_h, Origin="https://evil.example")) == 403, "")
        check("a rebinding Host header is refused on a loopback bind",
              call("/api/console?lite=1",
                   headers={"Host": "attacker.example:%d" % port}) == 403, "")
        check("a malformed GET answers 400 instead of dropping the connection",
              call("/api/gdtf/search?limit=abc") == 400, "")
        check("desktop only: no app manifest, no phone remote page",
              call("/manifest.webmanifest") == 404 and call("/remote.html") == 404, "")
        check("the network check answers (Settings -> Output)",
              call("/api/console/network") == 200, "")
        check("the open libraries are searchable over HTTP",
              call("/api/fixtures/library?q=intimidator") == 200, "")
        check("a bad library limit is a 400, not a crash",
              call("/api/fixtures/library?q=x&limit=abc") == 400, "")
        # The live stream: one connection carries state and light.
        import http.client as _hc
        conn = _hc.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/console/stream")
        resp = conn.getresponse()
        body = b""
        deadline = _time.monotonic() + 3
        while _time.monotonic() < deadline and not all(
                k in body for k in (b"event: snapshot", b"event: lite", b"event: look")):
            body += resp.read1(65536)
        # an edit sends only the parts it changed
        call("/api/console", {"action": "master", "params": {"level": 42}}, json_h)
        more = b""
        deadline = _time.monotonic() + 3
        while _time.monotonic() < deadline and b"event: snapdiff" not in more:
            more += resp.read1(65536)
        conn.close()
        diff_line = next((ln for ln in more.split(b"\n\n") if ln.startswith(b"event: snapdiff")), b"")
        diff = json.loads(diff_line.split(b"data: ", 1)[1]) if diff_line else {}
        check("after an edit a screen gets only the changed parts",
              diff.get("set", {}).get("master") == 42 and "patch" not in diff.get("set", {})
              and b"event: snapshot" not in more, str(diff)[:200])
        check("the live stream sends snapshot, lite and look events",
              resp.status == 200
              and resp.getheader("Content-Type", "").startswith("text/event-stream")
              and all(k in body for k in (b"event: snapshot", b"event: lite", b"event: look")),
              body[:120].decode("utf-8", "replace"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def test_fixture_kind(tmp: Path) -> None:
    """Every head knows what it physically is, so the 3D stage can draw it."""
    print("fixture kind + placement")
    from app import engine as eng_mod
    from app import fixture_kind as fk

    def kind(man, model, roles):
        return fk.describe({"manufacturer": man, "model": model,
                            "mode": "std", "map": roles})
    mover = ["pan", "tilt", "dimmer", "gobo"]
    cases = [
        ("Clay Paky", "Sharpy", mover, "moving_beam", "claypaky"),
        ("Martin", "MAC Aura XB", ["pan", "tilt", "dimmer", "zoom"], "moving_hybrid", "martin"),
        ("Robe", "Robin Spiider", ["pan", "tilt", "zoom", "red"], "moving_wash", "robe"),
        ("Chauvet DJ", "Intimidator Spot 260", mover, "moving_spot", "chauvet"),
        ("ETC", "Source Four LED", ["dimmer", "red"], "profile", "etc"),
        ("Chauvet DJ", "SlimPAR Pro H", ["dimmer", "red", "green", "blue"], "par", "chauvet"),
        ("Acme", "Unknown", ["red", "green", "blue"] * 8, "bar", "acme"),
        ("Martin", "Atomic 3000", ["dimmer", "strobe"], "strobe", "martin"),
        ("Astera", "AX1 PixelTube", ["dimmer", "red"], "tube", "astera"),
        ("Nobody", "Mystery", ["pan", "tilt", "dimmer", "zoom"], "moving_wash", "generic"),
        ("Nobody", "Dimmer", ["dimmer"], "par_can", "generic"),
    ]
    for man, model, roles, want_type, want_brand in cases:
        d = kind(man, model, roles)
        check(f"{man} {model} is a {want_type} by {want_brand}",
              d["type"] == want_type and d["brand"] == want_brand,
              f"{d['type']} / {d['brand']}")
    d = kind("Acme", "Unknown", ["red", "green", "blue"] * 8)
    check("a batten counts its cells", d["cells"] == 8, str(d["cells"]))
    d = kind("Robe", "Robin Spiider", ["pan", "tilt", "zoom", "red"])
    check("a zoom channel gives a beam range, a fixed lens does not",
          d["beam"]["max"] > d["beam"]["min"]
          and kind("ETC", "Source Four", ["dimmer"])["beam"]["max"]
          == kind("ETC", "Source Four", ["dimmer"])["beam"]["min"],
          json.dumps(d["beam"]))
    check("every brand carries styling the renderer can use",
          all(set(b) >= {"name", "body", "accent", "finish"}
              for b in fk.BRANDS.values()), "")
    check("a moving head that looks like a PAR by name is still drawn moving",
          kind("Acme", "Moving PAR", ["pan", "tilt", "red"])["moving"], "")

    spots = fk.place("moving_spot", 4, [], 10, 8)
    xs = sorted(s["x"] for s in spots)
    check("new heads are spread along a truss, not piled on one spot",
          len(set(xs)) == 4 and all(s["kind"] == "truss" for s in spots),
          str(spots))
    full = [{"x": x * 1.2, "y": 6.0, "z": 2.0} for x in range(-8, 9)]
    more = fk.place("moving_spot", 2, full, 10, 8)
    check("a full row spills onto a parallel row instead of stacking",
          all(m["z"] != 2.0 for m in more)
          and len({(m["x"], m["z"]) for m in more}) == 2, str(more))
    floor = fk.place("par", 2, [], 10, 8)
    check("uplights go on the floor", all(f["kind"] == "floor" for f in floor),
          str(floor))

    db = tmp / "kind.db"
    fixtures.seed_generics(db)
    e = eng_mod.Engine(db_path=db, dry_run=True, show_dir=tmp / "kind-shows")
    e.act("add_heads", query="Moving Head Spot", qty=3)
    pos = [(h["x"], h["y"], h["z"]) for h in e.patch]
    check("add_heads with no position hangs each head in its own place",
          len(set(pos)) == 3, str(pos))
    e.act("add_heads", query="Moving Head Spot", qty=1, x=1.5, y=0.5, z=3)
    h = e.patch[-1]
    check("an explicit position is kept", (h["x"], h["y"], h["z"]) == (1.5, 0.5, 3),
          str(h))
    snap = e.snapshot()
    check("the snapshot tells the stage what each head is",
          all(isinstance(p.get("body"), dict) and p["body"].get("type")
              for p in snap["patch"]), "")
    e.act("select_all")
    e.act("set_intensity", level=100)
    e.act("set_attribute", attribute="focus", value=128)
    looks = {r["n"]: r for r in e._looks()}
    check("the light feed carries beam shaping for the 3D beam",
          abs(looks[1].get("beam", {}).get("focus", -1) - 128 / 255) < 0.01,
          json.dumps(looks[1]))


def test_gdtf_share(tmp: Path) -> None:
    """The GDTF Share client, exercised end to end with NO network.

    The whole point of the feature is "other brands just work", and every
    way it can quietly fail is a way the operator sees an empty list and
    concludes the brand is unsupported:

      * a session that is not actually held, so the first search 401s;
      * an expired cookie reported as "no fixtures published";
      * a download that returns a JSON error page, which imported as a
        fixture would be a genuinely baffling bug;
      * a second revision of a light landing as a duplicate library
        entry, so the picker shows the same model twice and the operator
        cannot tell which modes are current.

    So the transport is injected.  The suite never opens a socket and
    never needs a GDTF Share account, but login, cookie expiry,
    auto-login-from-.env, search ranking, footprint filtering, caching,
    download, replacement and every error path all run for real.

    The cookie is the other half: it is a 2-hour token, and persisting it
    is what stops a server restart forcing a re-login, so that is checked
    by building a SECOND client over the same cache directory.
    """
    print("gdtf share (client, cache, download - no network)")
    import json as _json

    from app import fixtures as fx
    from app import gdtfshare as gs

    db = tmp / "share.db"
    fx.seed_generics(db)
    gdtf_bytes = _share_gdtf_bytes()

    def _catalogue():
        return _json.dumps({"result": True, "timestamp": 1672531200, "list": [
            {"rid": 11, "fixture": "Widget900", "manufacturer": "Acme",
             "revision": "r1", "rating": 4.5, "uploader": "Manuf.",
             "filesize": 1024,
             "modes": [{"name": "18ch", "dmxfootprint": 18}]},
            {"rid": 12, "fixture": "Spot 400", "manufacturer": "Acme",
             "revision": "r1", "rating": 3.0, "uploader": "User",
             "modes": [{"name": "8ch", "dmxfootprint": 8},
                       {"name": "32ch", "dmxfootprint": 32}]},
            {"rid": 13, "fixture": "Par 200", "manufacturer": "Bright Co",
             "revision": "r2", "rating": 4.9,
             "modes": [{"name": "5ch", "dmxfootprint": 5}]},
            # Spelled the way the real catalogue spells it, against a
            # sibling spelled the other way - the drift that hides a
            # fixture that is genuinely published.
            {"rid": 14, "fixture": "Slim Par T12 USB", "manufacturer": "Chauvet",
             "revision": "r1", "rating": 4.0,
             "modes": [{"name": "Default", "dmxfootprint": 8}]},
        ]}).encode("utf-8")

    def make(transport, cache="cache", user="me", password="pw"):
        return gs.GdtfShare(db, tmp / cache, user=user, password=password,
                            transport=transport)

    good = _share_transport(gdtf_bytes, _catalogue())
    c = make(good)

    # -- status is honest before anything has happened --------------------
    st = c.status()
    check("status before login: configured, not signed in",
          st["configured"] and not st["signed_in"], str(st))
    check("an empty catalogue is 0, never a failure",
          st["catalogue"] == 0 and not st["last_error"], str(st))

    # -- login -----------------------------------------------------------
    # A client with NO account must refuse without touching the network.
    # (With credentials it must NOT refuse - it signs in by itself, which
    # is what makes .env unattended operation work; checked further down.)
    bare = make(good, cache="cache-bare", user="", password="")
    try:
        bare.fetch_list()
        check("fetch with no account at all is refused", False, "no error raised")
    except gs.GdtfShareError as exc:
        check("fetch with no account at all is refused",
              exc.code == "no_session", exc.code)
    check("refusing makes no network request",
          all("gdtf-share.com" not in url for _m, url, _h, _b in good.calls),
          str([url for _m, url, _h, _b in good.calls]))

    c.set_credentials("me", "pw")
    check("login returns a summary", c.login()["ok"], "")
    check("the session cookie is kept", bool(c.cookies), str(c.cookies))
    check("status now reports a live session",
          c.status()["signed_in"] and c.status()["has_cookie"], str(c.status()))

    # -- search ----------------------------------------------------------
    r = c.search("widget")
    check("search by model name", r["total"] == 1 and
          r["results"][0]["fixture"] == "Widget900", str(r["total"]))
    check("search by manufacturer", c.search("", man="bright")["total"] == 1, "")
    check("a non-matching manufacturer filters everything out",
          c.search("", man="nosuchbrand")["total"] == 0, "")
    # The Share spells things inconsistently ("Slim Par T12 USB" vs
    # "SlimPAR Pro H USB"), so a plainly-typed query must still land.
    check("search ignores spacing drift in the catalogue",
          c.search("slimpart12")["total"] == 1,
          str(c.search("slimpart12")["total"]))
    check("search finds the spaced spelling when typed spaced too",
          c.search("slim par t12")["total"] == 1, "")
    check("an exact match still outranks a rescued one",
          gs.GdtfShare._score({"fixture": "Widget", "manufacturer": "Acme"},
                              "widget", "") <
          gs.GdtfShare._score({"fixture": "Widget 900", "manufacturer": "Acme"},
                              "widget", ""), "")
    check("an unrelated query still finds nothing",
          c.search("zzzznotathing")["total"] == 0, "")
    check("footprint filter uses any mode of the fixture",
          c.search("", man="acme", footprint=32)["total"] == 1, "")
    check("footprint filter excludes fixtures without that mode",
          c.search("", man="acme", footprint=5)["total"] == 0, "")
    best = c.search("")["results"]
    check("best rated sorts first when the match is equal",
          best[0]["fixture"] == "Par 200", str([b["fixture"] for b in best]))
    multi = next(x for x in best if x["fixture"] == "Spot 400")
    check("each result carries its modes and footprints",
          len(multi["modes"]) == 2 and multi["modes"][1]["dmxfootprint"] == 32,
          str(multi["modes"]))

    # -- .env credentials sign in by themselves, so a show machine that
    #    restarts never asks the operator to type a password -----------
    cold = make(good, cache="cache-cold")
    check("a cold client with .env credentials starts unsigned",
          not cold.status()["signed_in"], str(cold.status()))
    check("its first search signs in by itself",
          cold.search("widget")["total"] == 1, "")
    check("and it is signed in afterwards",
          cold.status()["signed_in"], str(cold.status()))

    # -- the catalogue is cached, not refetched per keystroke -------------
    before = len(good.calls)
    c.search("widget")
    c.search("par")
    check("repeat searches do not hit the network",
          len(good.calls) == before, "%d extra calls" % (len(good.calls) - before))
    check("a cached catalogue is reported as cached",
          c.search("widget")["cached"] is True, "")
    check("the catalogue size is visible in status",
          c.status()["catalogue"] == 4, str(c.status()["catalogue"]))

    # -- the cookie survives a restart (2-hour token, not a password) ----
    revived = gs.GdtfShare(db, tmp / "cache", transport=good)
    check("the session cookie is restored from disk",
          bool(revived.cookies), str(revived.cookies))
    check("a restored client needs no password to search",
          revived.search("widget")["total"] == 1, "")

    # -- download --------------------------------------------------------
    got = c.download(11)
    check("download installs the fixture", got["ok"] and got["model"] == "Widget900",
          str(got))
    check("download reports its modes", len(got["modes"]) == 1, str(got["modes"]))
    check("the fixture is searchable straight away",
          len(fx.search(db, "Widget")) == 1, "")
    again = c.download(11)
    check("re-downloading the same revision is a refresh, not a duplicate",
          again["refreshed"] and not again["replaced"] and
          len(fx.search(db, "Widget")) == 1, str(again))

    # A copy of the SAME fixture already in the library, from another
    # source, must not survive as a second entry - otherwise the picker
    # shows the model twice and there is no way to tell which modes are
    # current.  Rev 11 again is a refresh (above); this is the genuinely
    # different-revision case: Spot 400 arrives from a local file, then
    # from the Share under its own rid.
    stale = tmp / "spot400-local.gdtf"
    stale.write_bytes(_share_gdtf_bytes("Spot400"))
    fx.import_file(db, stale)
    check("a locally-imported copy is in the library",
          len(fx.search(db, "Spot400")) == 1, "")
    swapped = c.download(12)
    check("a Share copy replaces the local one instead of duplicating it",
          swapped["replaced"] and not swapped["refreshed"] and
          len(fx.search(db, "Spot400")) == 1, str(swapped))
    check("the replacement says so in its summary",
          "replaced" in swapped["summary"], swapped["summary"])
    check("downloading a different model does not disturb the first",
          len(fx.search(db, "Widget900")) == 1, "")
    check("the second fixture's modes came across",
          len(swapped["modes"]) == 1 and swapped["model"] == "Spot400",
          str(swapped))

    # -- a download that is really an error page -------------------------
    def json_error(method, url, **kw):
        if "login.php" in url:
            return _share_login_ok()
        if "getList.php" in url:
            return (200, {"content-type": "application/json"}, _catalogue())
        return (404, {"content-type": "application/json"},
                _json.dumps({"result": False,
                             "error": "File does not exist."}).encode("utf-8"))

    bogus = make(json_error, cache="cache-err")
    bogus.login()
    try:
        bogus.download(999999)
        check("a JSON error page is not imported as a fixture", False, "no raise")
    except gs.GdtfShareError as exc:
        check("a JSON error page is not imported as a fixture",
              exc.code == "not_found" and "does not exist" in exc.message,
              "%s / %s" % (exc.code, exc.message))
    check("a failed download adds nothing to the library",
          len(fx.search(db, "Widget")) == 1, str(len(fx.search(db, "Widget"))))

    # -- every failure says WHY, and never reads as "no fixtures" --------
    def expired(method, url, **kw):
        return (401, {"content-type": "application/json"},
                _json.dumps({"result": False, "error": "Unauthorized."}).encode("utf-8"))

    dead = make(expired, cache="cache-exp")
    dead.cookies["PHPSESSID"] = "stale"
    try:
        dead.fetch_list()
        check("an expired session is an explicit error", False, "no raise")
    except gs.GdtfShareError as exc:
        check("an expired session is an explicit error",
              exc.code == "unauthorized", exc.code)
    check("the stale cookie is dropped so the UI can offer a login",
          not dead.cookies, str(dead.cookies))
    check("status carries the reason to the UI",
          dead.status()["last_error_code"] == "unauthorized" and
          not dead.status()["signed_in"], str(dead.status()))

    def offline(method, url, **kw):
        raise gs.GdtfShareError("network", "cannot reach gdtf-share.com: offline")

    away = make(offline, cache="cache-net")
    away.cookies["PHPSESSID"] = "x"
    st = away.status()
    check("being offline is not reported as an empty catalogue",
          st["catalogue"] == 0 and not st["last_error"], str(st))
    try:
        away.fetch_list()
        check("being offline is a network error", False, "no raise")
    except gs.GdtfShareError as exc:
        check("being offline is a network error", exc.code == "network", exc.code)

    def broken(method, url, **kw):
        if "login.php" in url:
            return _share_login_ok()
        return (200, {"content-type": "application/json"}, b"<html>nope</html>")

    junk = make(broken, cache="cache-html")
    junk.login()
    try:
        junk.fetch_list()
        check("an HTML page instead of JSON is refused", False, "no raise")
    except gs.GdtfShareError as exc:
        check("an HTML page instead of JSON is refused",
              exc.code == "bad_response", exc.code)

    def bad_password(method, url, **kw):
        return (400, {"content-type": "application/json"},
                _json.dumps({"result": False,
                             "error": "No valid user or password provided."}).encode("utf-8"))

    wrong = make(bad_password, cache="cache-pw")
    try:
        wrong.login()
        check("a wrong password is reported in the operator's words", False, "no raise")
    except gs.GdtfShareError as exc:
        check("a wrong password is reported in the operator's words",
              exc.code == "unauthorized" and "No valid user" in exc.message,
              "%s / %s" % (exc.code, exc.message))

    blank = make(good, cache="cache-blank", user="", password="")
    try:
        blank.login()
        check("no credentials is its own error, not a 401", False, "no raise")
    except gs.GdtfShareError as exc:
        check("no credentials is its own error, not a 401",
              exc.code == "no_credentials", exc.code)

    # -- the library is kept to one row per model -------------------------
    # 4 built-in generics + Widget900 + Spot400, and no duplicates from
    # any of the re-imports above.
    check("the library holds one row per model, no duplicates",
          fx.count(db) == 6, "count=%s" % fx.count(db))
    check("remove_model names what it removed, and is empty when there is nothing",
          fx.remove_model(db, "Acme", "Widget900") == ["Acme Widget900"] and
          fx.remove_model(db, "Acme", "Widget900") == [], "")
    check("remove_model matches exactly by default",
          fx.remove_model(db, "Someone Else", "Widget900") == [], "")
    # The spelling drift the Share actually causes.  Chauvet publishes
    # "Slim Par T12 USB" on the Share and a curated library writes
    # "SlimPAR T12 USB", under a different manufacturer too.  An exact
    # match, or a case-insensitive one, leaves the SAME LIGHT in the
    # picker twice - so loose matching squashes spacing too.
    spaced = tmp / "spaced.gdtf"
    spaced.write_bytes(_share_gdtf_bytes("Slim Par T12 USB", maker="Chauvet"))
    fx.import_file(db, spaced)
    check("a differently-spaced model is found by loose matching",
          len(fx.model_sources(db, "chauvet", "slimpar t12 usb",
                               loose=True)) == 1,
          str(fx.model_sources(db, "chauvet", "slimpar t12 usb", loose=True)))
    check("loose removal supersedes it and says what it superseded",
          fx.remove_model(db, "acme", "SLIMPAR T12 USB", loose=True) ==
          ["Chauvet Slim Par T12 USB"],
          str(fx.remove_model(db, "acme", "SLIMPAR T12 USB", loose=True)))
    check("a different spacing of the same name is NOT a loose match",
          fx.model_sources(db, "acme", "totally other light", loose=True) == [], "")


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}


def test_api_auth() -> None:
    """The authentication boundary, tested by ATTACKING it.

    A static review of this repository found the gap; this suite exists so it
    cannot be reintroduced silently.  Every check here drives a real HTTP
    server bound to a non-loopback address with a token set - because that is
    the only configuration in which the boundary means anything.  On loopback
    `_authorised()` returns True for everything by design, so a test that ran
    against the default bind would pass against a server with no auth at all.
    """
    print("api auth (real HTTP, off-loopback, token set)")
    import os as _os
    import shutil as _shutil
    import subprocess as _subprocess
    import sys as _sys
    import tempfile as _tempfile
    import time as _time
    import urllib.error
    import urllib.request

    tmp = _tempfile.mkdtemp()
    port = 8971
    db = _os.path.join(tmp, "auth.db")
    TOKEN = "selftest-token-abc123"
    env = dict(_os.environ,
               PORT=str(port), HOST="0.0.0.0", CONSOLE_TOKEN=TOKEN,
               CONSOLE_DRY_RUN="true", FIXTURE_DB=db,
               CONSOLE_SHOW_DIR=_os.path.join(tmp, "shows"),
               CONSOLE_AUTOSAVE="false", MIDI_ENABLED="false",
               GDTF_SHARE_USER="", GDTF_SHARE_PASSWORD="")
    proc = _subprocess.Popen(
        [_sys.executable, _os.path.join("app", "main.py")],
        env=env, cwd=str(ROOT),
        stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL)

    def call(path, method="POST", body=None, token=None):
        url = "http://127.0.0.1:%d%s" % (port, path)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token is not None:
            req.add_header("X-Jarvis-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()
        except Exception as exc:                      # not up yet
            return 0, str(exc)

    try:
        up = False
        for _ in range(40):
            if proc.poll() is not None:
                break
            if call("/api/status", "GET")[0] == 200:
                up = True
                break
            _time.sleep(0.4)
        check("the test server started, off-loopback, with a token set", up,
              "HOST=0.0.0.0 PORT=%d" % port)
        if not up:
            return

        # ---- the gate itself -------------------------------------------
        code, _ = call("/api/status", "GET")
        check("GET /api/status is public, so the shell renders before a token",
              code == 200, "HTTP %s" % code)
        code, _ = call("/api/fixtures?q=LED", "GET")
        check("GET /api/fixtures is public - it is only a search box",
              code == 200, "HTTP %s" % code)
        code, _ = call("/api/console", "POST", {"action": "status", "params": {}})
        check("POST /api/console is blocked with no token", code == 401,
              "HTTP %s" % code)

        # ---- the bug, in the four shapes it took ----------------------
        # Each of these mutates the PERSISTENT fixture library, which decides
        # every head's channel-to-role map - so this is not an info leak, it
        # is a way to change what the engine will be told to do.
        for route, body in (
            ("/api/fixtures/create",
             {"manufacturer": "EvilCo", "model": "Backdoor 9000",
              "mode": "8ch", "channels": ["Dimmer", "Red"]}),
            ("/api/fixtures/channel", {"mode_id": 1, "channel": 0, "label": "X"}),
            ("/api/fixtures/range",
             {"mode_id": 1, "channel": 0, "min": 0, "max": 9}),
            ("/api/fixtures/import", {"path": "nope.gdtf"}),
        ):
            code, body_text = call(route, "POST", body)
            # 401 is the ONLY correct answer.  A 400 is a validation error
            # AFTER the handler ran, which is the bug this test exists for -
            # so it is called out separately rather than lumped in.
            check("POST %s is blocked with no token" % route, code == 401,
                  "HTTP %s %s" % (code, body_text[:80]))
            if code != 401:
                check("  ...and it did not reach its handler either",
                      code == 400,
                      "HTTP %s means the handler RAN and then refused: %s"
                      % (code, body_text[:100]))

        # ...and it really did write nothing.  A 401 that still committed
        # would be a worse bug than the one being fixed.
        if _os.path.exists(db):
            import sqlite3
            conn = sqlite3.connect(db)
            evil = conn.execute(
                "SELECT count(*) FROM fixtures WHERE manufacturer='EvilCo'"
            ).fetchone()[0]
            conn.close()
            check("and nothing reached the database", evil == 0,
                  "%d EvilCo row(s) written by an unauthenticated call" % evil)
        else:
            check("and nothing reached the database (no db file was created)",
                  True, "")

        # ---- a valid token still works ---------------------------------
        code, _ = call("/api/console", "POST",
                       {"action": "status", "params": {}}, token=TOKEN)
        check("a valid token is accepted", code == 200, "HTTP %s" % code)
        code, _ = call("/api/console", "POST",
                       {"action": "status", "params": {}}, token="wrong")
        check("a wrong token is refused", code == 401, "HTTP %s" % code)
        code, _ = call("/api/console", "POST",
                       {"action": "status", "params": {}}, token="")
        check("an empty token is refused", code == 401, "HTTP %s" % code)

        # ---- the token must not travel in a URL -----------------------
        # It used to be accepted as ?token=, which puts a credential that
        # drives real fixtures into history, Referer headers and access logs.
        code, _ = call("/api/console?token=%s" % TOKEN, "POST",
                       {"action": "status", "params": {}})
        check("the token is NOT accepted in the query string", code == 401,
              "HTTP %s - a URL credential ends up in history and logs" % code)

        # ---- an unknown endpoint is authenticated, not public ----------
        # The safe default: a route nobody has written yet is private until
        # somebody deliberately makes it public.
        code, _ = call("/api/console/does-not-exist", "POST", {})
        check("an unknown /api/ endpoint is authenticated, not public",
              code in (401, 404), "HTTP %s" % code)
        code, _ = call("/api/anything-at-all", "POST", {})
        check("including one that does not exist yet", code in (401, 404),
              "HTTP %s" % code)

        # ---- the static pages are still open --------------------------
        # A 401 on the HTML would leave the operator with no way to enter a
        # token, which is a lockout rather than a security measure.
        code, _ = call("/", "GET")
        check("the console page itself is still served without a token",
              code == 200, "HTTP %s" % code)
        code, _ = call("/app/main.js", "GET")
        check("and its script", code == 200, "HTTP %s" % code)

        # ---- response headers -----------------------------------------
        req = urllib.request.Request("http://127.0.0.1:%d/" % port)
        with urllib.request.urlopen(req, timeout=15) as resp:
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
        check("the page cannot be framed - it has GO LIVE and BLACKOUT on it",
              "frame-ancestors" in hdrs.get("content-security-policy", ""),
              hdrs.get("content-security-policy", "<no CSP header>"))
        check("and is not sniffable into a different type",
              hdrs.get("x-content-type-options") == "nosniff",
              hdrs.get("x-content-type-options", "<missing>"))
        check("and leaks no referrer, so a token cannot ride out in a URL",
              hdrs.get("referrer-policy") == "no-referrer",
              hdrs.get("referrer-policy", "<missing>"))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        _shutil.rmtree(tmp, ignore_errors=True)

    # ---- properties of the CODE, which a live run cannot show --------
    src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    # Grep the CODE, not the prose.  The comment that documents the old
    # prefix test quotes the old prefix test verbatim, so a naive substring
    # check reads the explanation of the bug as the bug - which is what the
    # first version of this check did, and it failed against correct code.
    code_only = "\n".join(ln for ln in src.splitlines()
                          if not ln.lstrip().startswith("#"))
    check("the token comparison is hmac.compare_digest, not ==",
          "hmac.compare_digest(" in code_only
          and 'X-Jarvis-Token") == token' not in code_only, "")
    check("and the docstring no longer CLAIMS constant time over a `==` - a "
          "comment that lies about a security property is a trap",
          "The token is compared in constant time" not in src, "")
    check("the public list is an explicit allow-list, not a prefix test",
          "PUBLIC_API_GET" in code_only and "_needs_auth" in code_only
          and 'route.startswith(("/api/console"' not in code_only, "")
    m = re.search(r"PUBLIC_API_GET = frozenset\(\{(.*?)\}\)", code_only, re.S)
    check("and it is short, deliberately: only the two read-only reads the "
          "shell needs before it has a token",
          m is not None and len(re.findall(r'"/api/', m.group(1))) == 2, "")
    check("the security headers are ONE method called from BOTH the 200 and "
          "the 304 path - the first version put them on the 304 only, so a "
          "normal page load carried none",
          "def _security_headers" in code_only, "")
    # The invariant is not "there are two call sites", it is "EVERY method
    # that sends a response sets them".  Counting call sites was true when
    # there were two, and stopped being true - correctly - when the model
    # route added a third, so the count needed replacing rather than
    # adjusting.  What actually matters is the failure this prevents: a new
    # response method that forgets, which is exactly what a count cannot see.
    methods = re.findall(r"    def (\w+)\(.*?\n(?=    def |\Z)", src, re.S)
    bare = [name for name in methods
            if "self.send_response(" in _method_body(src, name)
            and "self._security_headers()" not in _method_body(src, name)]
    check("and every method that sends a response sets them - checked by "
          "walking the methods, not by counting call sites, so a new "
          "response method that forgets is caught",
          not bare, "these send a response with no headers: %s" % (bare,))
    _file_body = _method_body(src, "_file")
    check("including _file, on BOTH the 200 and the 304 path",
          _file_body.count("self._security_headers()") >= 2,
          str(_file_body.count("self._security_headers()")))
    # ---- the foreign key, which the schema declared and never enforced
    import tempfile as _tf

    from app import fixtures as _fx
    tmp2 = _tf.mkdtemp()
    try:
        db2 = _os.path.join(tmp2, "fk.db")
        _fx.seed_generics(db2)
        conn = _fx.connect(db2)
        check("SQLite foreign keys are actually ON, not just declared in the "
              "schema (they are off by default, per connection)",
              conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "")
        victim = conn.execute("SELECT id FROM fixtures LIMIT 1").fetchone()[0]
        owned = conn.execute(
            "SELECT count(*) FROM modes WHERE fixture_id=?", (victim,)).fetchone()[0]
        conn.execute("DELETE FROM fixtures WHERE id=?", (victim,))
        conn.commit()
        left = conn.execute(
            "SELECT count(*) FROM modes WHERE fixture_id=?", (victim,)).fetchone()[0]
        conn.close()
        check("so ON DELETE CASCADE fires: deleting a profile takes its modes "
              "with it instead of orphaning them forever",
              owned > 0 and left == 0,
              "owned %d mode(s), %d left behind" % (owned, left))
    finally:
        _shutil.rmtree(tmp2, ignore_errors=True)


def _gdtf_zip(geometry: str, models=None, models_xml: str = "",
              channels: str = "", extra_files=None) -> bytes:
    """A synthetic GDTF archive.

    Built by hand rather than shipped, so a test can say exactly what it is
    asserting - and so the malformed cases are malformed ON PURPOSE rather
    than by accident.
    """
    models_xml = models_xml or (
        "<Models>"
        + "".join(
            '<Model File="%s" Name="%s" Width="0.2" Height="0.2" Length="0.2"/>'
            % (n, n) for n in (models or []))
        + "</Models>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<GDTF DataVersion="1.2"><FixtureType Name="TestCo" Manufacturer="TestCo" '
        'FixtureTypeID="Test" Model="Digital Twin">'
        '<Models_PLACEHOLDER/>'
        '<DMXModes><DMXMode Name="8ch" DMXChannels="%s">'
        '<DMXChannels>%s</DMXChannels></DMXMode></DMXModes>'
        '<Geometries>%s</Geometries>'
        '</FixtureType></GDTF>'
    ) % (channels, channels, geometry)
    if models_xml:
        xml = xml.replace("<Models_PLACEHOLDER/>", models_xml)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("description.xml", xml)
        for name, data in (models or {}).items() if isinstance(models, dict) else []:
            z.writestr(name, data)
        for name, data in (extra_files or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


_MOVER_GEOMETRY = (
    '<Geometry Model="Base" Name="Base" Position="'
    '{1,0,0,0}{0,1,0,0}{0,0,1,0}{0,0,0,1}">'
    '<Geometry Model="Yoke" Name="Yoke" Position="'
    '{1,0,0,0}{0,1,0,0}{0,0,1,-0.0934}{0,0,0,1}">'
    '<Geometry Model="Body" Name="Body" Position="'
    '{1,0,0,0}{0,1,0,0}{0,0,1,-0.1443}{0,0,0,1}">'
    '<Beam Model="Lens" Name="Lens" BeamAngle="12" FieldAngle="17" '
    'BeamRadius="0.03" LuminousFlux="48120" LampType="LED" Position="'
    '{1,0,0,0}{0,1,0,-0.025}{0,0,1,-0.1}{0,0,0,1}"/>'
    '</Geometry></Geometry></Geometry>')


def test_gdtf_geometry() -> None:
    """GDTF geometry -> a normalised, renderer-neutral definition.

    The real files on this machine are the ground truth where they exist:
    rev9044.gdtf is a Chauvet DJ Intimidator Spot 260 and carries a real
    Base/Yoke/Body/Lens chain with real pivots, and every one of these
    checks is either derived from it or deliberately contradicts it.
    """
    print("gdtf geometry (hierarchy, pivots, beams, safety, cache)")
    import shutil as _shutil
    import tempfile as _tempfile

    from app import gdtf_geom as G

    # ---- matrices ------------------------------------------------------
    ident = G.identity()
    check("identity is a real 4x4", len(ident) == 16 and ident[0] == 1.0
          and ident[5] == 1.0 and ident[10] == 1.0 and ident[15] == 1.0, "")
    m = G.parse_matrix("{1,0,0,0}{0,1,0,-0.025}{0,0,1,-0.1}{0,0,0,1}")
    check("a braced Position parses to 16 floats", len(m) == 16, str(m))
    # THE coordinate-system check.  GDTF is ROW-vector, so the translation
    # is at 3, 7 and 11 - NOT in the last column where OpenGL keeps it.
    # Reading it the other way is the classic "the fixture rotates about a
    # point nowhere near its own mechanism" bug, and it still moves, so it
    # is not obvious.
    check("GDTF is ROW-vector: the translation is at 3, 7 and 11",
          G.matrix_translation(m) == (0.0, -0.025, -0.1),
          str(G.matrix_translation(m)))
    check("and NOT at 12, 13, 14, which is the column-vector reading",
          (m[12], m[13], m[14]) == (0.0, 0.0, 0.0),
          "column-vector reading would give %s" % ((m[12], m[13], m[14]),))
    check("an absent Position is the identity, not a crash",
          G.parse_matrix(None) == ident and G.parse_matrix("") == ident, "")
    check("garbage is the identity rather than a partial matrix",
          G.parse_matrix("not a matrix") == ident, "")
    check("a wrong group count is the identity",
          G.parse_matrix("{1,0}{0,1}") == ident, "")
    prod = G.mat_mul(m, ident)
    check("multiplying by the identity changes nothing",
          all(abs(a - b) < 1e-9 for a, b in zip(prod, m)), "")

    # ---- hierarchy, from a synthetic mover ---------------------------
    raw = _gdtf_zip(_MOVER_GEOMETRY, models=["Base", "Yoke", "Body"])
    tmp = _tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "mover.gdtf")
        with open(path, "wb") as fh:
            fh.write(raw)
        d = G.build_definition(path, has_pan=True, has_tilt=True)
        check("a mover with geometry parses", d["ok"], d.get("reason", ""))
        g = d["geometry"]
        check("and produces exactly one root node", len(g["nodes"]) == 1, "")
        root = g["nodes"][0]
        check("whose children are the Yoke and the tail, in order",
              [c["name"] for c in root["children"]][:1] == ["Yoke"],
              str([c["name"] for c in root["children"]]))
        yoke = root["children"][0]
        body = yoke["children"][0]
        check("the Yoke holds the Body", yoke["name"] == "Yoke"
              and body["name"] == "Body", "")
        check("and the Body holds the beam", body["children"][0]["kind"] == "beam",
              "")
        # The real pivots, which is the whole point of reading them.
        check("the Yoke's pivot is 93.4 mm down, as the file says",
              abs(G.matrix_translation(yoke["matrix"])[2] + 0.0934) < 1e-6,
              str(G.matrix_translation(yoke["matrix"])))
        check("the Body's pivot is a further 144.3 mm down",
              abs(G.matrix_translation(body["matrix"])[2] + 0.1443) < 1e-6,
              str(G.matrix_translation(body["matrix"])))
        beam = body["children"][0]
        check("the beam is 25 mm across and 100 mm forward of the head",
              G.matrix_translation(beam["matrix"]) == (0.0, -0.025, -0.1),
              str(G.matrix_translation(beam["matrix"])))
        check("with the beam's real 12-degree core and 17-degree field",
              beam["beam_angle"] == 12.0 and beam["field_angle"] == 17.0
              and beam["beam_radius"] == 0.03, str(beam)[:120])
        check("and 48120 lumens of output", beam["luminous_flux"] == 48120.0, "")

        # ---- kinematics: DERIVED, not assumed ------------------------
        kin = g["kinematics"]
        check("pan is the YOKE, not the root - the root is the static base",
              G.node_at(g, kin["pan"])["name"] == "Yoke",
              str(kin))
        check("tilt is the BODY inside the yoke",
              G.node_at(g, kin["tilt"])["name"] == "Body", str(kin))
        check("and the root is not offered as either",
              kin["pan"] != "0" and kin["tilt"] != "0", str(kin))
        order = [(p, n["name"]) for p, n in G.rotation_nodes(g)]
        check("document order is what identifies them, so the list is "
              "Yoke, Body, then the tail",
              [n for _, n in order][:2] == ["Yoke", "Body"], str(order))
        # A mode with no Tilt channel must not grow a tilt node.
        g2 = G.parse_geometry(G.description_xml(path))
        k2 = G.resolve_kinematics(g2, has_pan=True, has_tilt=False)
        check("a mode with no Tilt channel gets no tilt node, so the solver "
              "is never asked to rotate a part that has no tilt control",
              k2["pan"] is not None and k2["tilt"] is None, str(k2))
        k3 = G.resolve_kinematics(g2, has_pan=False, has_tilt=False)
        check("and a mode with neither gets neither", k3["pan"] is None
              and k3["tilt"] is None, str(k3))

        # ---- the <Axis> spelling, and a non-moving fixture -----------
        wash = G.parse_geometry(
            G.description_xml(_write_gdtf(
                os.path.join(tmp, "wash.gdtf"),
                '<Geometry Model="Body" Name="Body" Position="'
                '{1,0,0,0}{0,1,0,0}{0,0,1,0}{0,0,0,1}">'
                '<Axis Model="Yoke" Name="Yoke" Position="'
                '{1,0,0,0}{0,1,0,0}{0,0,1,0.161}{0,0,0,1}"/>'
                '<Beam Model="Beam" Name="Beam" BeamAngle="60" FieldAngle="90" '
                'Position="{1,0,0,0}{0,1,0,0}{0,0,1,-0.134}{0,0,0,1}"/>'
                '</Geometry>', models=["Body", "Yoke", "Beam"])))
        check("a fixture that spells its yoke <Axis> is understood too",
              G.node_at(wash, G.resolve_kinematics(wash, True, False)["pan"])
              ["name"] == "Yoke", "")
        flat = G.parse_geometry(G.description_xml(_write_gdtf(
            os.path.join(tmp, "flat.gdtf"),
            '<Geometry Model="Body" Name="Body" Position="'
            '{1,0,0,0}{0,1,0,0}{0,0,1,0}{0,0,0,1}"/>', models=["Body"])))
        check("a fixture with no rotation nodes gets none, rather than a "
              "guess that would spin the whole thing",
              G.rotation_nodes(flat) == []
              and G.resolve_kinematics(flat, True, True)["pan"] is None, "")

        # ---- model files and the cache -------------------------------
        glb = _gdtf_zip(_MOVER_GEOMETRY, models=["Base", "Yoke", "Body"],
                        extra_files={"models/gltf/Body.glb": b"GLB\x01\x02",
                                     "models/gltf/Yoke.glb": b"GLB\x03\x04",
                                     "thumbnail.png": b"\x89PNG"})
        p2 = os.path.join(tmp, "glb.gdtf")
        with open(p2, "wb") as fh:
            fh.write(glb)
        cache = os.path.join(tmp, "cache")
        d2 = G.build_definition(p2, has_pan=True, has_tilt=True,
                                cache_root=cache)
        check("GLB models are found and extracted",
              set(d2["files"]) == {"Body", "Yoke"}, str(d2["files"]))
        check("a thumbnail is not mistaken for a model",
              "thumbnail" not in d2["files"], str(d2["files"]))
        # Three names for one file: the <Model Name> a node refers to, the
        # archive route the browser fetches, and where it landed on disk.
        # Getting this mapping wrong means the renderer fetches nothing,
        # which looks exactly like "this GDTF has no model".
        check("the stem a node refers to maps to the archive route AND the "
              "extracted path - both, because one without the other is "
              "useless to somebody",
              d2["files"]["Body"]["name"] == "models/gltf/Body.glb"
              and d2["files"]["Body"]["ext"] == ".glb"
              and os.path.isfile(d2["files"]["Body"]["path"]),
              str(d2["files"].get("Body")))
        check("and the extracted bytes are the ones from the archive",
              open(d2["files"]["Body"]["path"], "rb").read() == b"GLB\x01\x02", "")
        d2b = G.build_definition(p2, has_pan=True, has_tilt=True,
                                 cache_root=cache)
        check("a second build of the same file reuses the SAME cache key, so "
              "one definition is one load and many instances share it",
              d2b["key"] == d2["key"] and d2b["key"], "")
        check("and the definition id is stable enough to key a cache on",
              len(d2["key"]) == 16, d2["key"])
        # Three instances of one definition, one extraction.
        dirs = {G.build_definition(p2, True, True, cache_root=cache)["key"]
                for _ in range(3)}
        check("three builds share ONE cache directory", len(dirs) == 1, str(dirs))

        # ---- SECURITY: a GDTF is an untrusted archive ----------------
        hostile = io.BytesIO()
        with zipfile.ZipFile(hostile, "w") as z:
            z.writestr("description.xml", "<GDTF><FixtureType/></GDTF>")
            for evil in ("../../../../evil.glb", "..\\..\\evil.glb",
                         "/etc/passwd.glb", "C:/windows/evil.glb",
                         "models/../../../escape.glb"):
                z.writestr(evil, b"PWNED")
            z.writestr("models/ok.glb", b"GLB")
        p3 = os.path.join(tmp, "hostile.gdtf")
        with open(p3, "wb") as fh:
            fh.write(hostile.getvalue())
        ex = G.extract_models(p3, os.path.join(tmp, "hcache"))
        names = [os.path.basename(v) for v in ex["files"].values()]
        check("path traversal in a member name is refused - every variant",
              all("evil" not in n and "passwd" not in n for n in names),
              str(names))
        check("and the one legitimate model still comes out",
              any("ok" in n for n in names), str(names))
        cache_root = os.path.realpath(os.path.join(tmp, "hcache"))
        escaped = [v for v in ex["files"].values()
                   if not os.path.realpath(v).startswith(cache_root)]
        check("and nothing was written outside the cache directory",
              not escaped, str(escaped))
        check("an absolute member name is refused outright",
              G._safe_member_name("/etc/passwd.glb") is None
              and G._safe_member_name("C:/x.glb") is None
              and G._safe_member_name("..\\x.glb") is None, "")
        check("a normal name is allowed",
              G._safe_member_name("models/gltf/Body.glb") is not None, "")

        # ---- malformed input must never raise ------------------------
        for name, blob, why in (
            ("empty", b"", "an empty file"),
            ("not a zip", b"this is not a zip file at all", "plain text"),
            ("zip with no description.xml", None, "a missing description"),
        ):
            p = os.path.join(tmp, "%s.gdtf" % name.replace(" ", "_"))
            if blob is None:
                b = io.BytesIO()
                with zipfile.ZipFile(b, "w") as z:
                    z.writestr("thumbnail.png", b"\x89PNG")
                blob = b.getvalue()
            with open(p, "wb") as fh:
                fh.write(blob)
            try:
                d = G.build_definition(p)
                ok = d["ok"] is False and not d["geometry"]["nodes"]
            except Exception as exc:                     # noqa: BLE001
                ok = False
                why += " raised %s" % exc
            check("%s yields a definition with no geometry, not an exception"
                  % why, ok, "")

        p = os.path.join(tmp, "badxml.gdtf")
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("description.xml", "<GDTF><FixtureType>  <<< broken")
        with open(p, "wb") as fh:
            fh.write(b.getvalue())
        try:
            d = G.build_definition(p)
            check("invalid XML is reported, not raised", d["ok"] is False
                  and "XML" in d.get("reason", ""), str(d.get("reason")))
        except G.GdtfGeometryError:
            check("invalid XML is reported, not raised", True, "")
        except Exception as exc:                          # noqa: BLE001
            check("invalid XML is reported, not raised", False, repr(exc))

        # A model we cannot extract is a FALLBACK, not a failure: the
        # hierarchy, pivots and beam must survive.  A cache root under an
        # existing FILE is used because it is genuinely unwritable on every
        # platform - a "/proc/..." path is merely relative on Windows and
        # gets created happily, which made the first version of this test
        # pass for the wrong reason.
        d4 = G.build_definition(
            p2, has_pan=True, has_tilt=True,
            cache_root=os.path.join(path, "not-a-directory"))
        check("a cache root that cannot be written leaves the hierarchy and "
              "the beam intact - a fallback, not a failure",
              d4["ok"] and d4["geometry"]["nodes"]
              and d4["geometry"]["kinematics"]["pan"] and not d4["files"],
              str(d4.get("reason"))[:80])

        # ---- the REAL files, if this machine has any ------------------
        real = sorted(Path(ROOT / "data").rglob("*.gdtf"))
        if real:
            mover = None
            for path in real:
                d = G.build_definition(str(path), has_pan=True, has_tilt=True)
                if d["ok"] and (d["geometry"]["kinematics"].get("tilt")):
                    mover = (path, d)
                    break
            check("a real GDTF on this machine parses its geometry",
                  mover is not None, "%d file(s) found" % len(real))
            if mover:
                path, d = mover
                kin = d["geometry"]["kinematics"]
                pn = G.node_at(d["geometry"], kin["pan"])
                tn = G.node_at(d["geometry"], kin["tilt"])
                check("  and names a real pan node that is NOT the root",
                      pn is not None and kin["pan"] != "0",
                      "%s -> %s" % (path.name, pn["name"] if pn else None))
                check("  and a distinct tilt node below it",
                      tn is not None and tn["name"] != pn["name"],
                      "%s" % (tn["name"] if tn else None))
                beams = list(G.walk_beams(d["geometry"]))
                check("  with at least one beam carrying real angles",
                      beams and beams[0]["beam_angle"] > 0,
                      str([(b["beam_angle"], b["field_angle"]) for b in beams]))
        else:
            check("no real GDTF on this machine to cross-check against", True,
                  "(skipped, not failed)")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def _write_gdtf(path: str, geometry: str, models=None) -> str:
    blob = _gdtf_zip(geometry, models=models or ["Body"])
    with open(path, "wb") as fh:
        fh.write(blob)
    return path


def _method_body(src: str, name: str) -> str:
    """One `def` block out of a class, by indentation.

    Used by the security-header check, which needs to know which methods send
    a response and which of those forget the headers.  A regex over the
    whole file would match methods from other classes and from docstrings;
    bounding on the next line at the same indentation keeps it to the one
    method.
    """
    m = re.search(r"^    def %s\(.*?^(?=    def |\Z)" % re.escape(name),
                  src, re.S | re.M)
    return m.group(0) if m else ""


def test_fx_library() -> None:
    """Named effects, and the claim that an effect is offered only to a
    fixture that can actually do it.

    The promise the whole feature rests on.  A picker that lists Circle for
    a PAR is worse than one that omits it, because the operator finds out
    at 4pm on a rig that nothing happened.  So the filter is a pure
    function in `app/fxlib.py`, the engine and the UI both call it, and it
    is checked here against the rig's four real capability sets.

    The checks live in `tools/_fx_check.py` rather than inline because
    pasting them in means re-indenting every line by string surgery, and
    that is a reliable way to end up with a test that silently stopped
    running.
    """
    print("fx library (capability filter, purity, movement)")
    from tools import _fx_check

    _fx_check.run(check)

    print("fx engine path (available, start, skip, refuse, publish, expire)")
    from tools import _fx_engine_check

    _fx_engine_check.run(check)
