"""GDTF geometry -> a normalised, renderer-neutral fixture definition.

WHY THIS EXISTS.

`app/fixtures.py` reads DMX modes, channel roles and physical ranges out of
a GDTF.  It reads NO geometry at all: not `<Geometry>`, not `<Model>`, not
`<Beam>`, not `<Axis>`, not `<Emitter>`.  Every 3D body in the visualiser has
therefore been a generic primitive with a hard-coded shape per role - a
yoke that is a box, a head that is a sphere, and a PAN SPAN/TILT_SPAN
guessed at 270 degrees.

That guess is avoidable, because the real files carry it.  A Chauvet DJ
Intimidator Spot 260 (rev9044.gdtf) ships:

    <Geometry Model="Base" Position="...{0,0,1,0}">
      <Geometry Model="Yoke" Position="...{0,0,1,-0.0934}">
        <Geometry Model="Body" Position="...{0,0,1,-0.1443}">
          <Beam Model="Lens" BeamAngle="12" FieldAngle="17"
                BeamRadius="0.03" LuminousFlux="48120" .../>

That is the whole kinematic chain, in metres, with the pivots: the yoke
turns about the fixture's vertical axis 93.4 mm down from the base, the
head tilts about a horizontal axis a further 144.3 mm down, and the lens
sits 25 mm in front of the head with a 12-degree beam.  None of it needed
to be guessed, and all of it was being thrown away.

COORDINATE SYSTEMS - READ THIS BEFORE TOUCHING A MATRIX.

GDTF's `Position` attribute is FOUR braced groups of four:

    {m0,m1,m2,m3}{m4,m5,m6,m7}{m8,m9,m10,m11}{m12,m13,m14,m15}

and it is a ROW-VECTOR matrix: a point is transformed as `v * M`, so the
translation lives at indices 3, 7 and 11 - inside the basis rows, not in
the last column where a column-vector matrix keeps it.  Reading it as a
column vector (the OpenGL default) puts every pivot in the wrong place and
produces a fixture that rotates about a point nowhere near its own
mechanism, which is the single most common way a GDTF viewer looks wrong
and is hard to see because it still moves.

Units are METRES, and the visualiser's world is metres too, so no scaling
happens at the boundary.  +Z is "up" in GDTF's fixture space; the
visualiser's Y is up, so the instance transform does that one swap and
nothing else - it is done once, in the instance, not per node.

PAN/TILT NODES ARE DERIVED, NOT ASSUMED.

The spec is explicit that "pan = Y axis, tilt = X axis" is not a safe
assumption, and it is right: some fixtures express the yoke as `<Axis>`
and others as a nested `<Geometry>`.  So which node is which is decided by
the MODE's channels - if the mode has a Pan attribute, the first rotation
node is pan; if it also has Tilt, the next one is.  A fixture with no tilt
channel gets no tilt node, and the solver does not invent one.

SECURITY.

A GDTF is an untrusted archive from the internet.  Nothing here executes
any of it, nothing is written outside the cache directory, every member
name is rejected if it escapes it, and sizes are capped.  `extract_models`
is the only function that touches the filesystem and it is the only one
that needed the care.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree

# --- limits ---------------------------------------------------------------
# A GDTF that claims a 4 GB member is either broken or hostile, and either
# way we do not want to find out by filling the disk.
MAX_MEMBER_BYTES = 256 * 1024 * 1024      # one extracted model
MAX_TOTAL_BYTES = 512 * 1024 * 1024      # everything extracted from one file
MAX_XML_BYTES = 32 * 1024 * 1024         # description.xml
MAX_NODES = 4096                         # a fixture with 4k nodes is not real

# Extensions we will hand to a GPU loader.  Anything else in a GDTF is
# data (gobos, thumbnails, manuals) and is left alone.
MODEL_EXTS = (".glb", ".gltf", ".3ds", ".obj", ".stl")

_MATRIX_RE = re.compile(r"\{([^}]*)\}")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9 ._\-()\[\]]{1,180}$")


class GdtfGeometryError(ValueError):
    """A GDTF we could not read geometry out of.  Never fatal."""


# --- matrices -------------------------------------------------------------
def parse_matrix(text: str | None) -> list[float]:
    """GDTF's braced 4x4 into 16 floats, row-vector order preserved.

    The caller is responsible for knowing that GDTF is row-vector; this
    function does not transpose, because a silent transpose is the bug it
    exists to prevent.  See the module docstring.
    """
    if not text:
        return identity()
    groups = _MATRIX_RE.findall(text)
    if len(groups) != 4:
        # Some writers emit a flat 16-number list with no braces.  Parsed
        # defensively: a GDTF is untrusted input, so `float()` raising on
        # the word "not" is a crash in the one path that must never crash.
        # The whole matrix is simply not available, and identity is the
        # correct answer: the node still exists, still parents correctly,
        # and the fixture still renders.
        try:
            nums = [float(x) for x in re.split(r"[,\s]+", text.strip()) if x]
        except ValueError:
            return identity()
        if len(nums) == 16:
            return nums
        return identity()
    out: list[float] = []
    for g in groups:
        parts = [p for p in re.split(r"[,\s]+", g.strip()) if p]
        if len(parts) != 4:
            return identity()
        try:
            out.extend(float(p) for p in parts)
        except ValueError:
            return identity()
    return out


def identity() -> list[float]:
    return [1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def matrix_translation(m: list[float]) -> tuple[float, float, float]:
    """Translation of a ROW-VECTOR matrix: indices 3, 7, 11."""
    return (m[3], m[7], m[11])


def mat_mul(a: list[float], b: list[float]) -> list[float]:
    """Row-vector multiply: the transform for `a` THEN `b` is `b * a`."""
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[r * 4 + c] = sum(a[k * 4 + c] * b[r * 4 + k] for k in range(4))
    return out


def mat_mul_many(*mats: list[float]) -> list[float]:
    out = identity()
    for m in mats:
        if m:
            out = mat_mul(out, m)
    return out


# --- parsing --------------------------------------------------------------
def _f(node, attr: str, dflt: float = 0.0) -> float:
    try:
        return float(node.get(attr, ""))
    except (TypeError, ValueError):
        return dflt


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_beam(node) -> dict:
    """A `<Beam>`: the light source's own geometry and its output."""
    return {
        "kind": "beam",
        "name": node.get("Name") or node.get("Model") or "beam",
        "model": node.get("Model") or "",
        "matrix": parse_matrix(node.get("Position")),
        "beam_angle": _f(node, "BeamAngle", 0.0),      # degrees, the hot core
        "field_angle": _f(node, "FieldAngle", 0.0),    # degrees, the full cone
        "beam_radius": _f(node, "BeamRadius", 0.0),    # metres at the lens
        "luminous_flux": _f(node, "LuminousFlux", 0.0),
        "beam_type": node.get("BeamType") or "",
        "lamp_type": node.get("LampType") or "",
        "throw_ratio": _f(node, "ThrowRatio", 1.0),
        "rectangle_ratio": _f(node, "RectangleRatio", 0.0),
    }


def _parse_node(node, depth: int = 0) -> dict:
    tag = _local(node.tag)
    kind = {"Geometry": "node", "Axis": "axis", "Beam": "beam",
            "Node": "node"}.get(tag, "node")
    out = {
        "kind": kind,
        "name": node.get("Name") or node.get("Model") or kind,
        "model": node.get("Model") or "",
        "matrix": parse_matrix(node.get("Position")),
        "children": [],
    }
    if kind == "beam":
        out.update(_parse_beam(node))
        return out
    if depth >= 32:
        return out                      # a cycle guard, not a style choice
    for child in node:
        ctag = _local(child.tag)
        if ctag in ("Geometry", "Axis", "Beam", "Node"):
            out["children"].append(_parse_node(child, depth + 1))
    return out


def _collect_models(root) -> dict:
    out: dict[str, dict] = {}
    for holder in root.iter():
        if _local(holder.tag) != "Models":
            continue
        for m in holder:
            if _local(m.tag) != "Model":
                continue
            name = m.get("Name") or m.get("File") or ""
            if not name:
                continue
            out[name] = {
                "file": m.get("File") or name,
                "width": _f(m, "Width"), "height": _f(m, "Height"),
                "length": _f(m, "Length"),
                "primitive": m.get("PrimitiveType") or "",
            }
    return out


def _collect_emitters(root) -> list[dict]:
    out = []
    for holder in root.iter():
        if _local(holder.tag) != "Emitters":
            continue
        for em in holder:
            if _local(em.tag) != "Emitter":
                continue
            colour = [0.0, 0.0, 0.0]
            raw = (em.get("Color") or "").split(",")
            for i in range(min(3, len(raw))):
                try:
                    colour[i] = float(raw[i])
                except ValueError:
                    colour[i] = 0.0
            meas = []
            for m in em:
                if _local(m.tag) == "Measurement":
                    meas.append({"physical": _f(m, "Physical"),
                                 "luminous": _f(m, "LuminousIntensity")})
            out.append({"name": em.get("Name") or "",
                        "colour": colour, "measurements": meas})
    return out


def parse_geometry(desc_xml: bytes) -> dict:
    """`description.xml` -> a normalised definition.

    Returns `{"nodes": [...], "models": {...}, "emitters": [...],
    "kinematics": {...}}`.  `nodes` is empty for a GDTF with no geometry,
    which is a normal state and not an error - the visualiser's generic
    primitives are the documented fallback.
    """
    if not desc_xml or len(desc_xml) > MAX_XML_BYTES:
        return _empty()
    try:
        root = ElementTree.fromstring(desc_xml)
    except ElementTree.ParseError as exc:
        raise GdtfGeometryError("description.xml is not valid XML: %s" % exc)

    nodes: list[dict] = []
    for holder in root.iter():
        if _local(holder.tag) != "Geometries":
            continue
        for g in holder:
            if _local(g.tag) == "Geometry":
                nodes.append(_parse_node(g))
                if len(nodes) >= MAX_NODES:
                    break

    # A top-level <Geometry> directly under <FixtureType> (no <Geometries>
    # wrapper) is legal in older revisions.
    if not nodes:
        for holder in root.iter():
            if _local(holder.tag) in ("FixtureType", "FixtureTypeName"):
                for g in holder:
                    if _local(g.tag) == "Geometry":
                        nodes.append(_parse_node(g))
    return {
        "nodes": nodes,
        "models": _collect_models(root),
        "emitters": _collect_emitters(root),
        "kinematics": {},          # filled in by resolve_kinematics
    }


def _empty() -> dict:
    return {"nodes": [], "models": {}, "emitters": [], "kinematics": {}}


# --- kinematics -----------------------------------------------------------
def _walk(node: dict, path: str, acc: list[tuple[str, dict]]):
    acc.append((path, node))
    for i, c in enumerate(node.get("children") or []):
        _walk(c, "%s/%d" % (path, i), acc)


def rotation_nodes(root: dict) -> list[tuple[str, dict]]:
    """Every node that COULD be pan or tilt, in document order, root excluded.

    Document order is the only thing that identifies them: GDTF defines the
    first rotation node of a fixture as pan and the next as tilt, and the
    two spellings (`<Axis>` and a nested `<Geometry>`) are interchangeable
    in practice, so they are collected into one list and told apart by
    ORDER rather than by tag.

    The ROOT is excluded, and that exclusion is the whole difference between
    right and wrong.  On the Intimidator the tree is Base -> Yoke -> Body ->
    Lens, and what each part IS:

        Base   the static bottom, bolted to the truss
        Yoke   rotates for PAN
        Body   rotates for TILT, inside the yoke
        Lens   the beam

    Including the root made pan the BASE - so a pan move spun the bottom of
    the fixture while the yoke stood still.  It moves, so it looks plausible;
    it is just the wrong part, which is the failure this whole system exists
    to avoid.
    """
    acc: list[tuple[str, dict]] = []
    for i, n in enumerate(root.get("nodes") or []):
        for j, c in enumerate(n.get("children") or []):
            _collect_rotation(c, "%d/%d" % (i, j), acc)
    return acc


def _collect_rotation(node: dict, path: str, acc: list[tuple[str, dict]]):
    if node.get("kind") in ("axis", "node"):
        acc.append((path, node))
    for i, c in enumerate(node.get("children") or []):
        _collect_rotation(c, "%s/%d" % (path, i), acc)


def resolve_kinematics(root: dict, has_pan: bool, has_tilt: bool) -> dict:
    """Which node is pan and which is tilt - decided by the MODE, not guessed.

    `has_pan`/`has_tilt` come from the mode's channel attributes.  A fixture
    with no Tilt channel gets no tilt node, so the solver is never asked to
    rotate something that has no tilt control, and a wash with a yoke does
    not grow a fake head.
    """
    nodes = rotation_nodes(root)
    out: dict = {"pan": None, "tilt": None, "order": []}
    idx = 0
    if has_pan and idx < len(nodes):
        out["pan"] = nodes[idx][0]
        out["order"].append("pan")
        idx += 1
    if has_tilt and idx < len(nodes):
        out["tilt"] = nodes[idx][0]
        out["order"].append("tilt")
    return out


def node_at(root: dict, path: str | None) -> dict | None:
    if not path:
        return None
    node = None
    for i, n in enumerate(root.get("nodes") or []):
        if str(i) == path:
            return n
    parts = path.split("/")
    node = (root.get("nodes") or [None])[int(parts[0])] if root.get("nodes") else None
    for p in parts[1:]:
        if not node:
            return None
        node = (node.get("children") or [None])[int(p)]
    return node


# --- model extraction, safely ---------------------------------------------
def _safe_member_name(name: str) -> str | None:
    """Reject anything that could escape the destination directory.

    A GDTF is attacker-controlled: it is a ZIP from a website, and a member
    named `../../../.ssh/authorized_keys` is the oldest trick there is.
    Three separate checks, because one is not enough:
      * no absolute paths, no drive letters, no UNC;
      * no `..` segment anywhere;
      * the resolved destination must still be inside the target.
    """
    if not name or name.endswith("/"):
        return None
    if name.startswith("/") or name.startswith("\\"):
        return None
    if re.match(r"^[A-Za-z]:", name):          # C:\ or C:/
        return None
    norm = name.replace("\\", "/")
    if ".." in norm.split("/"):
        return None
    if not _SAFE_NAME.match(os.path.basename(norm)):
        return None
    return norm


def model_members(zf: zipfile.ZipFile) -> list[str]:
    """The model files in an archive, as archive-relative names."""
    out = []
    for info in zf.infolist():
        safe = _safe_member_name(info.filename)
        if not safe:
            continue
        if info.file_size > MAX_MEMBER_BYTES:
            continue
        low = safe.lower()
        if low.endswith(MODEL_EXTS) or "/models/" in low:
            if low.endswith(MODEL_EXTS):
                out.append(safe)
    return out


def extract_models(gdtf_path: str | os.PathLike, dest_root: str | os.PathLike,
                   key: str | None = None) -> dict:
    """Extract the model files, ONCE, into a content-addressed cache.

    Returns `{"key", "dir", "files": {name: abs path}}`.

    The cache is keyed by a hash of the GDTF bytes, which is what makes
    "one definition, one load, many instances" true at the FILE level as
    well as in the browser: a rig with forty heads of the same type
    extracts its geometry once and every instance reads the same files.
    """
    gdtf_path = Path(gdtf_path)
    dest_root = Path(dest_root)
    raw = gdtf_path.read_bytes()
    if key is None:
        key = hashlib.sha256(raw).hexdigest()[:16]
    dest = (dest_root / key).resolve()
    # Belt and braces: even with _safe_member_name, confirm the resolved
    # directory is the one we meant.
    root_resolved = dest_root.resolve()
    if root_resolved not in dest.parents and dest != root_resolved:
        raise GdtfGeometryError("cache path escaped the destination root")

    files: dict[str, str] = {}
    try:
        zf = zipfile.ZipFile(gdtf_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise GdtfGeometryError("not a readable archive: %s" % exc)
    with zf:
        members = model_members(zf)
        if not members:
            return {"key": key, "dir": str(dest), "files": {}}
        total = 0
        dest.mkdir(parents=True, exist_ok=True)
        for name in members:
            info = zf.getinfo(name)
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                break
            target = (dest / name).resolve()
            if root_resolved not in target.parents and target != root_resolved:
                continue                      # refused again, at the last step
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.stat().st_size == info.file_size:
                files[name] = str(target)     # already extracted
                continue
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 64 * 1024)
            files[name] = str(target)
    return {"key": key, "dir": str(dest), "files": files}


def description_xml(gdtf_path: str | os.PathLike) -> bytes:
    """`description.xml` from an archive, or b"" if it is not there.

    The name is exact: the spec's older drafts say `*.gdtf`, and a reader
    that guesses gets an empty profile and blames the file.
    """
    try:
        zf = zipfile.ZipFile(gdtf_path)
    except (zipfile.BadZipFile, OSError):
        return b""
    with zf:
        for name in ("description.xml", "Description.xml"):
            try:
                data = zf.read(name)
            except KeyError:
                continue
            if len(data) <= MAX_XML_BYTES:
                return data
    return b""


def model_index(extracted: dict, declared: dict | None = None) -> dict:
    """The cache's file list, keyed by the `<Model Name>` a node refers to.

    Three names for one file, and getting the mapping wrong means the
    renderer fetches nothing at all while looking perfectly healthy.

    A node says `Model="Base"`.  `<Models>` says
    `Name="Base" File="Chauvet DJ Intimidator Spot 260 Base"`.  The archive
    holds `models/3ds/Chauvet DJ Intimidator Spot 260 Base.3ds`.  The cache
    holds the same file under a content-addressed directory.  So the only
    thing that connects the node to the file is the `<Models>` declaration -
    and the first version of this function ignored it and keyed by the file
    stem instead, which meant every key was the long name and every node
    lookup missed.  Nothing errored: four models downloaded, parsed and sat
    in memory, and the renderer drew nothing.

    `Lens` is the case that shows the declaration is doing real work: the
    Intimidator's Beam node refers to `Model="Lens"`, for which the profile
    ships no model file at all.  It is the lens, not a part, and "no such
    model" is the correct and complete answer for it.

    Each entry carries all three names, and which one you want is explicit:

        {"Body": {"name": "models/3ds/... Body.3ds",   # the fetch route
                  "path": "C:/.../cache/ab12../...Body.3ds",
                  "ext": ".3ds"}}

    The first version returned just the archive-relative name, which is right
    for the browser and useless to the server: a caller holding it could not
    open the file.  All three, and the mistake cannot be made twice.
    """
    by_stem: dict[str, tuple[str, str, str]] = {}
    for rel, abs_path in (extracted.get("files") or {}).items():
        base = os.path.basename(rel)
        stem, ext = os.path.splitext(base)
        by_stem[stem] = (rel, abs_path, ext.lower())

    declared = declared or {}
    out: dict[str, dict] = {}
    used: set[str] = set()
    # Declared models first: the Name is what a node refers to, so that is
    # the key.  Matched on the File attribute, which is a stem in every file
    # seen so far but is not guaranteed to be one.
    for name, meta in declared.items():
        want = os.path.splitext(
            os.path.basename(str(meta.get("file") or name)))[0]
        hit = by_stem.get(want)
        if hit:
            out[name] = {"name": hit[0], "path": hit[1], "ext": hit[2]}
            used.add(want)
    # Then only what is genuinely LEFT OVER, so a GDTF with models and no
    # <Models> block still reaches the renderer.  Testing "not already in
    # `out`" is not enough: a declared entry is keyed by its Name, so that
    # file's STEM is absent from `out` and gets added a second time - which
    # is how this served EIGHT model keys for FOUR files and had the browser
    # fetch, parse and upload every part twice.
    for stem, hit in by_stem.items():
        if stem not in used and stem not in out:
            out[stem] = {"name": hit[0], "path": hit[1], "ext": hit[2]}
    return out


def build_definition(gdtf_path: str | os.PathLike, has_pan: bool = False,
                     has_tilt: bool = False,
                     cache_root: str | os.PathLike | None = None) -> dict:
    """Everything the frontend needs about one fixture type, in one dict."""
    xml = description_xml(gdtf_path)
    if not xml:
        return {"geometry": _empty(), "models": {}, "emitters": [],
                "files": {}, "key": "", "ok": False,
                "reason": "no description.xml in the archive"}
    try:
        geom = parse_geometry(xml)
    except GdtfGeometryError as exc:
        return {"geometry": _empty(), "models": {}, "emitters": [],
                "files": {}, "key": "", "ok": False, "reason": str(exc)}
    geom["kinematics"] = resolve_kinematics(geom, has_pan, has_tilt)
    out = {"geometry": geom, "models": geom["models"],
           "emitters": geom["emitters"], "files": {}, "key": "", "ok": True,
           "reason": ""}
    if cache_root is not None:
        try:
            ex = extract_models(gdtf_path, cache_root)
            out["files"] = model_index(ex, geom.get("models"))
            out["key"] = ex["key"]
        except (GdtfGeometryError, OSError) as exc:
            # A model we cannot extract is a FALLBACK, not a failure: the
            # fixture still gets its hierarchy, its pivots and its beam.
            #
            # OSError is in the tuple deliberately.  The obvious way to make
            # a cache root unwritable - a path under a file, or a read-only
            # volume - raises from mkdir() or open() INSIDE extract_models,
            # not from the zipfile call the original except clause covered.
            # A full disk or a permissions problem must not take down the
            # visualiser, which is the whole point of the fallback chain.
            out["reason"] = "models not extracted: %s" % exc
    return out


def summarise(definition: dict) -> str:
    """One line, for a log and for the fallback badge in the UI."""
    geom = definition.get("geometry") or {}
    n = len(geom.get("nodes") or [])
    if not n:
        return "no geometry in the profile"
    beams = sum(1 for _ in walk_beams(geom))
    kin = geom.get("kinematics") or {}
    return "%d node(s), %d beam(s), pan=%s tilt=%s, %d model file(s)" % (
        n, beams, kin.get("pan") or "-", kin.get("tilt") or "-",
        len(definition.get("files") or {}))


def walk_beams(root: dict):
    def rec(node):
        if node.get("kind") == "beam":
            yield node
        for c in node.get("children") or []:
            yield from rec(c)
    for n in root.get("nodes") or []:
        yield from rec(n)


if __name__ == "__main__":        # a tiny probe, for the console
    import sys
    for path in sys.argv[1:]:
        d = build_definition(path, has_pan=True, has_tilt=True)
        print("%-22s ok=%-5s %s" % (os.path.basename(path), d["ok"],
                                    d["reason"] or summarise(d)))


# --- definition resolution: head -> shared, cached definition -------------
#
# "One definition, one load, many instances" has to be true in the SERVER too
# or the manifest route re-parses a 450 KB archive on every request.  The
# cache is keyed by definition id and holds the built definition, so the
# second head of the same type costs nothing.
#
# WHAT A DEFINITION ID IS.  A head that came from the Share already knows its
# file (`model_source()` returns `rev9044.gdtf`, and it is already a column
# in the patch sheet), so the id is that file's stem - stable, short, and
# identical for every head of that type.  A hand-written profile has no
# file, so its id is `man/model/mode`, which is equally stable and equally
# shared.  Neither encodes a head number, which is the thing that must NOT
# be in there or nothing would ever be shared.
_MANIFEST_CACHE: dict[str, dict] = {}
_MANIFEST_LIMIT = 64          # a rig has a handful of types, not thousands


def definition_id(head: dict) -> str:
    """A stable, shareable id for the TYPE this head is."""
    src = str(head.get("source") or "").strip()
    if src.lower().endswith(".gdtf") and Path(src).stem:
        return Path(src).stem
    man = str(head.get("manufacturer") or "").strip()
    model = str(head.get("model") or "").strip()
    mode = str(head.get("mode") or "").strip()
    if man or model:
        return "/".join((man, model, mode)).strip("/")
    return "head-%s" % head.get("head_no", "?")


def _head_roles(head: dict) -> tuple[bool, bool]:
    """Does this head have a pan / a tilt channel, from its resolved map?"""
    roles = set(head.get("map") or [])
    return "pan" in roles, "tilt" in roles


def manifest_for(head: dict, gdtf_dir: str | os.PathLike,
                 cache_dir: str | os.PathLike,
                 manifest_of: str | None = None) -> dict:
    """The one definition for this head's type, built at most once.

    `manifest_of` lets a caller pass the source filename when the head dict
    does not carry one, which is the case for heads restored from an autosave
    written before this existed.
    """
    src = manifest_of or str(head.get("source") or "").strip()
    did = definition_id(head) if not manifest_of else Path(manifest_of).stem
    hit = _MANIFEST_CACHE.get(did)
    if hit is not None:
        return hit

    path = Path(gdtf_dir) / (src if src.lower().endswith(".gdtf")
                             else src + ".gdtf")
    if src and path.is_file():
        has_pan, has_tilt = _head_roles(head)
        built = build_definition(path, has_pan=has_pan, has_tilt=has_tilt,
                                 cache_root=cache_dir)
        built["source"] = path.name
    else:
        # No file.  A hand-written profile, a built-in generic, or a profile
        # whose .gdtf was deleted since import.  All three are the SAME
        # case to a renderer - there is no geometry - and saying so plainly
        # is what puts the fallback badge in the corner of the fixture.
        has_pan, has_tilt = _head_roles(head)
        built = {"geometry": {**_empty(),
                              "kinematics": ({"pan": None, "tilt": None,
                                              "order": []} if (has_pan
                                                              or has_tilt)
                                             else {})},
                 "models": {}, "emitters": [], "files": {}, "key": "",
                 "ok": False,
                 "reason": ("no .gdtf for %s" % did) if not has_pan
                           else "no .gdtf for %s (but it pans and tilts)" % did}
        built["source"] = ""
    built["id"] = did
    if len(_MANIFEST_CACHE) >= _MANIFEST_LIMIT:
        _MANIFEST_CACHE.clear()       # a rig does not have thousands of types
    _MANIFEST_CACHE[did] = built
    return built


def clear_manifest_cache() -> None:
    """Forget every built definition - on re-import, or on a fixture change."""
    _MANIFEST_CACHE.clear()


def public_manifest(built: dict) -> dict:
    """The definition as the BROWSER gets it: no filesystem paths.

    The absolute path each model was extracted to is useful to this process
    and meaningless - a leak, in fact - to a page.  The browser is handed
    the archive-relative name and fetches it back through a route, so the
    layout of the server's disk is not part of the API.

    `modelMeta` is the profile's DECLARED physical size for each model, in
    metres, and it matters more than it looks.  The meshes do not agree on
    units - the Intimidator's Base.3ds parses to bounds exactly 1000x the
    same profile's declared Length/Width/Height - so the declared size is
    what the renderer fits the mesh to.  Without these numbers the only way
    to place a model is to guess.
    """
    geom = built.get("geometry") or {}
    declared = geom.get("models") or {}
    return {
        "id": built.get("id") or "",
        "ok": bool(built.get("ok")),
        "reason": built.get("reason") or "",
        "source": built.get("source") or "",
        "nodes": geom.get("nodes") or [],
        "kinematics": geom.get("kinematics") or {},
        "beams": [b for b in walk_beams(geom)],
        "emitters": built.get("emitters") or [],
        "modelMeta": {
            name: {"width": m.get("width", 0.0),
                   "height": m.get("height", 0.0),
                   "length": m.get("length", 0.0),
                   "primitive": m.get("primitive", "")}
            for name, m in declared.items()
        },
        "models": {
            name: {"name": m["name"], "ext": m["ext"]}
            for name, m in (built.get("files") or {}).items()
        },
    }
