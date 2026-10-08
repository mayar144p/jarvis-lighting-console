"""MVR (My Virtual Rig) in and out - backlog A10 item 3.

An MVR file is a zip: GeneralSceneDescription.xml (the plot: every light
with its GDTF file, mode, DMX address and place; trusses; layers and
groups that may nest) plus the GDTF files themselves and 3D models.
Vectorworks, Capture, grandMA3, Depence and others read and write it.

In: every light is installed from the GDTF inside the file (into the same
folder as GDTF Share downloads, so its real 3D body works too), patched at
its universe and address and placed where the plot has it, hanging or
standing; lights hung in a row become a truss they are mounted on.  The
whole import is one undo step.

Out: the patch and the rigging as an MVR, with the GDTF files the desk has
for those lights.

Coordinates.  MVR: millimetres, Z up, the audience towards -Y.  Here:
metres, y up, x across, z from the back wall (the stage) towards the
audience.  So x -> x, z -> height, and -y -> z, shifted into the room.
"""
from __future__ import annotations

import io
import math
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

SCENE = "GeneralSceneDescription.xml"
MAX_BYTES = 500 * 1024 * 1024          # a plot with its 3D models
MAX_XML = 50 * 1024 * 1024
MAX_GDTF = 60 * 1024 * 1024

ROW_HEIGHT = 0.3                       # lights this close in height and depth...
ROW_DEPTH = 0.45                       # ...hang on one truss
ROW_GAP = 4.5                          # unless there is a gap this wide between them


# -- reading -------------------------------------------------------------------
def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _child(el, name):
    return next((c for c in el if _tag(c) == name), None)


def _text(el, name, default="") -> str:
    c = _child(el, name)
    return (c.text or "").strip() if c is not None and c.text else default


def parse_matrix(text: str | None) -> tuple[list[list[float]], list[float]]:
    """'{u}{v}{w}{o}' -> (3x3 rotation as rows u, v, w; offset in mm)."""
    nums = re.findall(r"\{([^}]*)\}", text or "")
    vecs = []
    for n in nums[:4]:
        try:
            vecs.append([float(x) for x in n.split(",")][:3])
        except ValueError:
            vecs.append([0.0, 0.0, 0.0])
    if len(vecs) != 4 or any(len(v) != 3 for v in vecs):
        return [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0.0, 0.0, 0.0]
    return vecs[:3], vecs[3]


def _compose(parent, local):
    """A child's place in the world from its parent's and its own."""
    (pr, po), (lr, lo) = parent, local
    # MVR rows are the local axes in the parent's frame: p_world = po + lo . pr
    rot = [[sum(lr[i][k] * pr[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    off = [po[j] + sum(lo[k] * pr[k][j] for k in range(3)) for j in range(3)]
    return rot, off


def parse_address(text: str) -> tuple[int, int] | None:
    """'1.001' / '2.17' or an absolute number ((universe - 1) * 512 + address)."""
    t = str(text or "").strip()
    try:
        if "." in t:
            u, a = t.split(".", 1)
            u, a = int(u), int(a)
        else:
            n = int(t)
            if n < 1:
                return None
            u, a = (n - 1) // 512 + 1, (n - 1) % 512 + 1
    except ValueError:
        return None
    return (u, a) if u >= 1 and 1 <= a <= 512 else None


def read(data: bytes | Path) -> dict:
    """The plot in an MVR file: lights, trusses and the GDTF files inside."""
    raw = Path(data).read_bytes() if isinstance(data, (str, Path)) else data
    if len(raw) > MAX_BYTES:
        raise ValueError(f"an MVR file is at most {MAX_BYTES // 2 ** 20} MB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("not an MVR file (it isn't a zip)") from None
    with zf:
        names = {n.replace("\\", "/"): n for n in zf.namelist()}
        scene = next((names[n] for n in names if n.rsplit("/", 1)[-1] == SCENE), None)
        if scene is None:
            raise ValueError("not an MVR file (no GeneralSceneDescription.xml inside)")
        if zf.getinfo(scene).file_size > MAX_XML:
            raise ValueError("the plot inside the MVR is too big")
        xml = zf.read(scene)
        if b"<!DOCTYPE" in xml[:2000] or b"<!ENTITY" in xml[:20000]:
            raise ValueError("the plot uses XML features the desk doesn't read")
        root = ET.fromstring(xml)
        gdtf = {}
        for n, real in names.items():
            if n.lower().endswith(".gdtf") and zf.getinfo(real).file_size <= MAX_GDTF:
                gdtf[n.rsplit("/", 1)[-1]] = zf.read(real)
    lights, trusses, objects, others = [], [], [], 0
    ident = ([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0.0, 0.0, 0.0])

    def walk(el, frame, layer):
        nonlocal others
        for c in el:
            t = _tag(c)
            if t == "ChildList":
                walk(c, frame, layer)
                continue
            if t not in ("Fixture", "Truss", "SceneObject", "GroupObject", "Support", "VideoScreen", "Projector"):
                continue
            m = _child(c, "Matrix")
            here = _compose(frame, parse_matrix(m.text if m is not None else None)) if m is not None else frame
            name = c.get("name") or ""
            if t == "Fixture":
                addrs = _child(c, "Addresses")
                addr = None
                if addrs is not None:
                    first = next((a for a in addrs if _tag(a) == "Address"), None)
                    if first is not None:
                        addr = parse_address(first.text or "")
                rot, off = here
                lights.append({
                    "name": name, "uuid": c.get("uuid") or "", "layer": layer,
                    "gdtf": _text(c, "GDTFSpec"), "mode": _text(c, "GDTFMode"),
                    "fid": _text(c, "FixtureID"), "address": addr,
                    "pos": [off[0] / 1000, off[1] / 1000, off[2] / 1000],
                    # GDTF draws a light hanging; its own up (w) pointing down = standing
                    "stance": "stand" if rot[2][2] < -0.5 else "hang",
                })
            elif t == "Truss":
                trusses.append({"name": name, "pos": [v / 1000 for v in here[1]], "u": here[0][0],
                                "gdtf": _text(c, "GDTFSpec"), "layer": layer})
            elif t in ("SceneObject", "VideoScreen") and object_kind(name, t):
                objects.append({"name": name, "kind": object_kind(name, t), "pos": [v / 1000 for v in here[1]],
                                "u": here[0][0], "gdtf": _text(c, "GDTFSpec")})
            elif t != "GroupObject":
                others += 1
            walk(c, here, layer)

    scene_el = next((c for c in root if _tag(c) == "Scene"), None)
    for layers in (scene_el if scene_el is not None else []):
        if _tag(layers) != "Layers":
            continue
        for layer in layers:
            if _tag(layer) == "Layer":
                m = _child(layer, "Matrix")
                frame = _compose(ident, parse_matrix(m.text)) if m is not None else ident
                walk(layer, frame, layer.get("name") or "")
    ver = f"{root.get('verMajor', '?')}.{root.get('verMinor', '?')}"
    for item in trusses + objects:
        item["size"] = model_size(gdtf.get(item["gdtf"].rsplit("/", 1)[-1]) or gdtf.get(item["gdtf"] + ".gdtf")) \
            if item["gdtf"] else None
    return {"version": ver, "lights": lights, "trusses": trusses, "objects": objects, "others": others, "gdtf": gdtf}


# -- trusses and scene objects -------------------------------------------------------
# What a scene object is, from its name (Vectorworks and Capture name them
# after the symbol: "PA Left", "Bar", "DJ Riser", "LED Wall 4x3").
_OBJ_WORDS = [
    ("screen", r"\bscreen|led ?wall|video ?wall|\bled panel|projection"),
    ("sub", r"\bsubs?\b|subwoofer"),
    ("speaker", r"speaker|\bpa\b|\bpa[ _-]|line ?array|\bmonitor|wedge|loudspeaker"),
    ("dj_booth", r"\bdj\b|dj ?booth|dj ?table|turntable|cdj"),
    ("bar", r"\bbar\b|bar ?counter"),
    ("riser", r"riser|\bstage\b|\bdeck|platform|podium|rostrum"),
    ("pillar", r"pillar|column|\bpost\b"),
    ("table", r"\btable"),
]
# their size when the file doesn't say (w x d x h m)
_OBJ_SIZE = {"screen": (4.0, 0.2, 2.5), "sub": (0.8, 0.8, 0.6), "speaker": (0.6, 0.5, 1.0),
             "dj_booth": (2.0, 0.9, 1.1), "bar": (4.0, 0.8, 1.1), "riser": (3.0, 2.0, 0.4),
             "pillar": (0.4, 0.4, 3.0), "table": (1.6, 0.8, 0.75)}


def object_kind(name: str, tag: str = "SceneObject") -> str | None:
    """Our object kind for an MVR scene object, or None (left out)."""
    if tag == "VideoScreen":
        return "screen"
    for kind, pat in _OBJ_WORDS:
        if re.search(pat, str(name or ""), re.I):
            return kind
    return None


def model_size(blob: bytes | None) -> tuple[float, float, float] | None:
    """(length, width, height) in m of the biggest model in a GDTF file -
    a truss's length, a screen's width.  None when it doesn't say."""
    if not blob:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            name = next((n for n in zf.namelist() if n.rsplit("/", 1)[-1] == "description.xml"), None)
            if name is None or zf.getinfo(name).file_size > MAX_XML:
                return None
            xml = zf.read(name)
        if b"<!DOCTYPE" in xml[:2000] or b"<!ENTITY" in xml[:20000]:
            return None
        best = None
        for el in ET.fromstring(xml).iter():
            if _tag(el) != "Model":
                continue
            try:
                dims = tuple(float(el.get(k) or 0) for k in ("Length", "Width", "Height"))
            except ValueError:
                continue
            if all(d > 0 for d in dims) and (best is None or dims[0] * dims[1] * dims[2] > best[0] * best[1] * best[2]):
                best = dims
        return best
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError):
        return None


def length_in_name(name: str) -> float | None:
    """'Truss 3m', 'Box truss 2.5 m', 'Pipe (4.00 m)', '10ft truss' -> metres."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(m|meter|metre|ft|')(?![a-z])", str(name or ""), re.I)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    if m.group(2).lower() in ("ft", "'"):
        v *= 0.3048
    return v if 0.3 <= v <= 60 else None


def _yaw(u: list[float]) -> float:
    """Our rigging / object turn (degrees) from an MVR object's own x axis."""
    return math.degrees(math.atan2(-float(u[1]), float(u[0]))) if abs(u[0]) + abs(u[1]) > 1e-6 else 0.0


def safe_name(name: str) -> str | None:
    """A GDTF file's name, safe to write into our folder (or None)."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9 ._@()+-]", "_", base).strip(" .")
    if not base.lower().endswith(".gdtf") or len(base) > 120 or base.startswith("."):
        return None
    return base


# -- placing in our room -----------------------------------------------------------
def to_room(plot: dict, room: dict) -> dict:
    """Where everything goes in our coordinates, and the room it needs."""
    pts = [lt["pos"] for lt in plot["lights"]] + [t["pos"] for t in plot["trusses"]] \
        + [o["pos"] for o in plot.get("objects") or []]
    if not pts:
        return {"lights": [], "trusses": [], "objects": [], "room": None}
    xs, ys, zs = [p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts]
    xmid, ymax = (min(xs) + max(xs)) / 2, max(ys)
    width = max(float(room.get("width") or 0), (max(xs) - min(xs)) + 4.0, 8.0)
    depth = max(float(room.get("depth") or 0), (ymax - min(ys)) + 6.0, 8.0)
    height = max(float(room.get("height") or 0), max(zs) + 1.0, 3.0)
    back = float(room.get("back") if room.get("back") is not None else -1.0)
    cx = float(room.get("cx") or 0.0)
    def here(p):
        x, y, z = p
        return {"x": round(cx + x - xmid, 3), "y": round(max(0.0, z), 3), "z": round(back + 1.0 + (ymax - y), 3)}
    out = [{**lt, **here(lt["pos"])} for lt in plot["lights"]]
    trusses = [{**t, **here(t["pos"]), "rot": round(_yaw(t.get("u") or [1, 0, 0]), 2)} for t in plot.get("trusses") or []]
    objects = [{**o, **here(o["pos"]), "rot": round(_yaw(o.get("u") or [1, 0, 0]), 2)} for o in plot.get("objects") or []]
    return {"lights": out, "trusses": trusses, "objects": objects,
            "room": {"width": round(width, 2), "depth": round(depth, 2), "height": round(height, 2)}}


def rows(lights: list[dict]) -> list[list[dict]]:
    """Hung lights in a line across the room (same height and depth, no big
    gaps): each becomes a truss.  Lone lights and floor lights stay free."""
    hung = sorted((lt for lt in lights if lt["stance"] == "hang" and lt["y"] > 1.5), key=lambda lt: (lt["y"], lt["z"], lt["x"]))
    groups: list[list[dict]] = []
    for lt in hung:
        for g in groups:
            if abs(g[0]["y"] - lt["y"]) <= ROW_HEIGHT and abs(g[0]["z"] - lt["z"]) <= ROW_DEPTH \
                    and min(abs(o["x"] - lt["x"]) for o in g) <= ROW_GAP:
                g.append(lt)
                break
        else:
            groups.append([lt])
    return [sorted(g, key=lambda lt: lt["x"]) for g in groups if len(g) >= 2]


# -- writing -------------------------------------------------------------------------
def _matrix(x: float, y: float, z: float, standing: bool = False, yaw: float = 0.0) -> str:
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    u, v, w = [c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]
    if standing:                     # turned over: upright on the floor
        v, w = [s, -c, 0.0], [0.0, 0.0, -1.0]
    f = lambda vec: "{" + ",".join(f"{a:.6f}" for a in vec) + "}"    # noqa: E731
    return f(u) + f(v) + f(w) + "{" + f"{x * 1000:.3f},{-z * 1000:.3f},{y * 1000:.3f}" + "}"


def _uuid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "jarvis-mvr:" + seed)).upper()


def write(patch: list[dict], rigging: list[dict], gdtf_files: dict[str, Path], show: str = "show",
          objects: list[dict] | None = None) -> bytes:
    """An MVR of the patch and the rigging.  `gdtf_files`: (manufacturer,
    model) -> the .gdtf the desk has for it (put inside the file)."""
    root = ET.Element("GeneralSceneDescription", verMajor="1", verMinor="6", provider="Jarvis", providerVersion="1")
    scene = ET.SubElement(root, "Scene")
    layers = ET.SubElement(scene, "Layers")
    layer = ET.SubElement(layers, "Layer", name="Lights", uuid=_uuid(show + ":layer"))
    kids = ET.SubElement(layer, "ChildList")
    for r in rigging:
        if r.get("kind") not in ("truss", "pipe", "bar"):
            continue
        a, b = r["a"], r["b"]
        mx, my, mz = ((a[i] + b[i]) / 2 for i in range(3))
        yaw = math.degrees(math.atan2(-(b[2] - a[2]), b[0] - a[0]))
        length = math.dist((a[0], a[2]), (b[0], b[2]))
        name = str(r.get("name") or r["id"])
        if length_in_name(name) is None:
            name = f"{name} ({length:.2f} m)"         # the length, for the desk (and a person) reading it back
        t = ET.SubElement(kids, "Truss", name=name, uuid=_uuid(show + ":rig:" + r["id"]))
        ET.SubElement(t, "Matrix").text = _matrix(mx, my, mz, yaw=yaw)
    for o in objects or []:
        if o.get("kind") not in _OBJ_SIZE:
            continue
        label = str(o.get("name") or o["kind"].replace("_", " ").title())
        if object_kind(label) != o["kind"]:
            label = f"{label} ({o['kind'].replace('_', ' ')})"
        so = ET.SubElement(kids, "VideoScreen" if o["kind"] == "screen" else "SceneObject", name=label,
                           uuid=_uuid(show + ":obj:" + str(o.get("id"))))
        ET.SubElement(so, "Matrix").text = _matrix(float(o.get("x") or 0), float(o.get("y") or 0), float(o.get("z") or 0),
                                                   yaw=-float(o.get("rot") or 0))
    packed: dict[str, str] = {}
    for h in patch:
        key = (str(h.get("manufacturer") or ""), str(h.get("model") or ""))
        src = gdtf_files.get(key)
        spec = src.name if src else safe_name(f"{key[0]}@{key[1]}.gdtf") or "fixture.gdtf"
        if src:
            packed[spec] = str(src)
        f = ET.SubElement(kids, "Fixture", name=str(h.get("name") or h.get("model") or f"Light {h['head_no']}"),
                          uuid=_uuid(f"{show}:head:{h['head_no']}"))
        ET.SubElement(f, "Matrix").text = _matrix(float(h.get("x") or 0), float(h.get("y") or 0), float(h.get("z") or 0),
                                                  standing=h.get("stance") == "stand")
        ET.SubElement(f, "GDTFSpec").text = spec
        ET.SubElement(f, "GDTFMode").text = str(h.get("mode") or "")
        ET.SubElement(f, "FixtureID").text = str(h["head_no"])
        ET.SubElement(f, "UnitNumber").text = str(h["head_no"])
        addrs = ET.SubElement(f, "Addresses")
        ET.SubElement(addrs, "Address", **{"break": "0"}).text = f"{int(h.get('universe') or 1)}.{int(h.get('address') or 1)}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(SCENE, b'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n' + ET.tostring(root, encoding="utf-8"))
        for name, path in packed.items():
            zf.write(path, name, compress_type=zipfile.ZIP_STORED)       # a .gdtf is a zip already
    return buf.getvalue()


# -- onto the desk -------------------------------------------------------------------
def import_into(eng, data: bytes, gdtf_dir: Path, replace: bool = False) -> dict:
    """Patch and place an MVR plot on the desk (one undo step).
    `replace`: a new show from the plot - the lights and the rigging there
    are now go first, and the room is sized to the plot."""
    import hashlib

    from . import engine as engine_mod
    from . import fixtures
    plot = read(data)
    if not plot["lights"]:
        raise ValueError("there are no lights in this MVR file")
    gdtf_dir = Path(gdtf_dir)
    gdtf_dir.mkdir(parents=True, exist_ok=True)
    # 1. each light type, from its GDTF inside the file
    types: dict[str, int] = {}
    problems: list[str] = []
    for spec in sorted({lt["gdtf"] for lt in plot["lights"] if lt["gdtf"]}):
        blob = plot["gdtf"].get(spec.rsplit("/", 1)[-1]) or plot["gdtf"].get(spec.rsplit("/", 1)[-1] + ".gdtf")
        name = safe_name(spec if spec.lower().endswith(".gdtf") else spec + ".gdtf")
        if blob is None or name is None:
            problems.append(f"{spec}: its GDTF file isn't in the MVR")
            continue
        path = gdtf_dir / name
        if path.is_file() and path.read_bytes() != blob:     # another light with the same file name
            path = gdtf_dir / f"{path.stem}-{hashlib.sha256(blob).hexdigest()[:8]}.gdtf"
        if not path.is_file():
            path.write_bytes(blob)
        try:
            done = fixtures.store_parsed(eng.db_path, fixtures.parse_gdtf(path), path.name)
        except (ValueError, ET.ParseError, zipfile.BadZipFile, KeyError) as exc:
            problems.append(f"{spec}: {exc}")
            continue
        fid = ((done.get("imported") or [{}])[0]).get("fixture_id")
        if fid:
            types[spec] = fid
    fixtures.invalidate_cache()
    engine_mod._FIXTURE_CACHE.clear()
    with eng.lock:
        before, top = eng._undo_state(), (eng._undo[-1] if eng._undo else None)
    from . import venue as venue_mod
    if replace:
        eng.act("patch_clear")
        with eng.lock:
            v = venue_mod.normalise(eng.venue)
            v["rigging"] = []
            eng._set_venue_doc(v)
    room = venue_mod.normalise(eng.venue)["room"]
    placed = to_room(plot, {**room, "width": 0, "depth": 0, "height": 0} if replace else room)
    want = placed["room"]
    if want and (replace or want["width"] > (room.get("width") or 0) + 0.01 or want["depth"] > (room.get("depth") or 0) + 0.01
                 or want["height"] > (room.get("height") or 0) + 0.01):
        eng.act("venue_room", width=want["width"], depth=want["depth"], height=want["height"])
    # 2a. the plot's own trusses, at their place, angle and length; a light
    #     hung on one goes on it (the turn of the truss turns the light)
    on_rig: dict = {}
    trusses = 0
    placed_lights = [lt for lt in placed["lights"] if lt["gdtf"] in types]
    for i, t in enumerate(placed.get("trusses") or []):
        size = t.get("size")
        length = (size[0] if size else None) or length_in_name(t["name"]) or 2.0
        th = math.radians(t["rot"])
        dx, dz = math.cos(th), math.sin(th)
        # the lights along it: within 0.6 m of its line, about its height
        near = []
        for lt in placed_lights:
            if (lt["uuid"] or id(lt)) in on_rig or lt["stance"] == "stand" and lt["y"] < 1.5:
                continue
            along = (lt["x"] - t["x"]) * dx + (lt["z"] - t["z"]) * dz
            off = abs(-(lt["x"] - t["x"]) * dz + (lt["z"] - t["z"]) * dx)
            if off <= 0.6 and abs(along) <= length / 2 + 0.5 and abs(lt["y"] - t["y"]) <= 1.0:
                near.append((lt, along))
        trim = (min(lt["y"] for lt, _a in near) if near else t["y"]) - 0.15
        r = eng.act("rig_add", preset="straight", piece="box30", x=t["x"], z=t["z"], length=length,
                    rot=t["rot"], trim=max(0.3, trim), name=(t["name"] or f"Truss {i + 1}")[:40])
        if not r.get("ok"):
            continue
        trusses += 1
        for lt, along in near:
            on_rig[lt["uuid"] or id(lt)] = (r["id"], max(0.0, min(1.0, along / length + 0.5)))
    # 2b. the hung lights left in a row, on a truss of their own (as before)
    for i, row in enumerate(rows([lt for lt in placed_lights if (lt["uuid"] or id(lt)) not in on_rig])):
        x0, x1 = row[0]["x"] - 0.5, row[-1]["x"] + 0.5
        z = sum(lt["z"] for lt in row) / len(row)
        trim = min(lt["y"] for lt in row)
        name = row[0]["layer"] if row[0]["layer"] and row[0]["layer"].lower() not in ("lights", "layer", "fixtures") else ""
        r = eng.act("rig_add", preset="straight", piece="box30", x=(x0 + x1) / 2, z=z, length=x1 - x0, trim=trim,
                    name=name or f"Truss {trusses + 1} (MVR)")
        if not r.get("ok"):
            continue
        trusses += 1
        for lt in row:
            on_rig[lt["uuid"] or id(lt)] = (r["id"], max(0.0, min(1.0, (lt["x"] - x0) / max(0.01, x1 - x0))))
    # 2c. the scene objects the desk knows (PA, bar, DJ booth, riser, screen...)
    objects = 0
    for o in placed.get("objects") or []:
        w, d, h = o.get("size") or _OBJ_SIZE.get(o["kind"], (1.0, 1.0, 1.0))
        if o["kind"] == "screen":
            d, h = min(d, 0.3), h                      # a screen's depth is its thickness
        r = eng.act("venue_add", item={"kind": o["kind"], "name": (o["name"] or "")[:40], "x": o["x"], "z": o["z"],
                                       "y": o["y"] if o["kind"] in ("screen", "speaker") and o["y"] > 0.3 else 0,
                                       "w": w, "d": d, "h": h, "rot": o["rot"]})
        objects += bool(r.get("ok"))
    # 3. every light: patched at its address, placed where the plot has it
    heads: list[int] = []
    moved: list[str] = []
    for lt in placed["lights"]:
        fid = types.get(lt["gdtf"])
        if fid is None:
            continue
        params = {"fixture_id": fid, "qty": 1, "name": (lt["name"] or "")[:40] or None,
                  "x": lt["x"], "y": lt["y"], "z": lt["z"]}
        if lt["mode"]:
            params["mode"] = lt["mode"]
        if lt["address"]:
            params["universe"], params["address"] = lt["address"]
        r = eng.act("add_heads", **params)
        if not r.get("ok") and "mode" in params:
            params.pop("mode")
            r = eng.act("add_heads", **params)
            if r.get("ok"):
                moved.append(f"{lt['name'] or lt['gdtf']}: mode {lt['mode']!r} not in its file - its first mode")
        if not r.get("ok") and "address" in params:
            want_at = f"{params.pop('universe')}.{params.pop('address')}"
            r = eng.act("add_heads", **params)
            if r.get("ok"):
                moved.append(f"{lt['name'] or lt['gdtf']}: {want_at} was taken - patched at the next free address")
        if not r.get("ok"):
            problems.append(f"{lt['name'] or lt['gdtf']}: {r.get('error')}")
            continue
        n = r["heads"][0]
        heads.append(n)
        rig = on_rig.get(lt["uuid"] or id(lt))
        if rig:
            eng.act("set_place", head=n, rig=rig[0], t=rig[1])
        else:
            eng.act("set_place", head=n, x=lt["x"], y=lt["y"], z=lt["z"], stance=lt["stance"])
    # one undo step for the whole plot
    from .assistant import _collapse
    _collapse(eng, top, before, "Import MVR" if heads else None)
    skipped = len(plot["lights"]) - len(heads)
    summary = f"MVR: {len(heads)} light(s) patched and placed" + (f", {trusses} truss(es)" if trusses else "") \
        + (f", {objects} object(s)" if objects else "") + (f"; {skipped} not imported" if skipped else "")
    return {"ok": bool(heads), "heads": heads, "trusses": trusses, "objects": objects, "types": len(types), "skipped": skipped,
            "problems": problems[:40], "notes": moved[:40], "version": plot["version"], "summary": summary,
            **({} if heads else {"error": "no light could be imported: " + "; ".join(problems[:3])})}


def gdtf_for_patch(eng, gdtf_dirs: list[Path]) -> dict:
    """(manufacturer, model) -> the .gdtf file the desk has for it."""
    out = {}
    for h in eng.patch:
        key = (str(h.get("manufacturer") or ""), str(h.get("model") or ""))
        if key in out:
            continue
        src = eng.model_source(h)
        if src.lower().endswith(".gdtf"):
            for d in gdtf_dirs:
                p = Path(d) / src
                if p.is_file():
                    out[key] = p
                    break
    return out


def export_from(eng, gdtf_dirs: list[Path], show: str = "") -> bytes:
    from . import venue as venue_mod
    with eng.lock:
        patch = [dict(h) for h in eng.patch]
        v = venue_mod.normalise(eng.venue)
        rigging, objects = list(v.get("rigging") or []), list(v.get("objects") or [])
    return write(patch, rigging, gdtf_for_patch(eng, gdtf_dirs), show or eng.show_file or "show", objects)
