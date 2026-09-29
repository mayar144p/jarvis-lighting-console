'''Build synthetic 3DS / GLB bytes and a manifest, for the twin test.'''

import base64
import io
import json
import os
import struct
import zipfile


def three_ds(tri=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
              faces=((0, 1, 2),), pad=b"") -> bytes:
    """A minimal 3DS chunk file, in the layout the REAL files use.

    Hand-built so the parser is exercised against bytes we control.  The
    alternative - a real .gdtf out of the operator's library - is gitignored
    data, so a test reading it passes here and fails for everyone else.  The
    truss-bar test did exactly that and was only caught by cloning the
    commit somewhere clean.

    Every detail below is one the real Intimidator Base.3ds taught us the
    hard way, and each is a thing a plausible-looking implementation gets
    wrong while still passing a naive test:

    * The faces chunk is 0x4120.  Accepting 0x4140 as well is not leniency,
      it is how the wrong chunk gets in - the 6856-byte 0x4140 in the real
      file satisfies "length == 8 + count*8" exactly.

    * A face record is FOUR uint16s and the FLAGS WORD IS LAST:
      `(i, j, k, flags)`.  Flags-first skips a word that is not there.

    * The chunk is LONGER than its faces.  The real one is 3472 bytes for
      344 faces occupying 2760, so a parser that requires
      `length == 8 + count*8` rejects the genuine chunk.  `pad` reproduces
      that trailing slack.

    * MORE THAN ONE FACE.  With a single face there is exactly one flags
      word, and a parser that skips the wrong end still lands on the right
      answer.  Six faces and a distinctive flags value means a wrong
      ordering reads 0xBEEF as a vertex index and fails loudly.

    The count is a uint16, so "<HIH" (8 bytes) and NOT "<HII" (10): the
    parser reads the count as u16 at +6, so a 4-byte count puts the real
    value in the wrong half of the field and every chunk looks empty.
    """
    verts = tri
    vchunk = (struct.pack("<HIH", 0x4110, 8 + len(verts) * 12, len(verts))
              + b"".join(struct.pack("<fff", *v) for v in verts))
    fchunk = (struct.pack("<HIH", 0x4120, 8 + len(faces) * 8 + len(pad),
                          len(faces))
              + b"".join(struct.pack("<4H", *(tuple(f) + (0xBEEF,)))
                         for f in faces)
              + pad)
    body = vchunk + fchunk
    mesh = struct.pack("<HI", 0x4100, 6 + len(body)) + body
    obj = (struct.pack("<H", 0) + b"\0" * 26      # name, 26 bytes, empty
           + struct.pack("<I", 0)                  # nmatids
           + struct.pack("<H", 0)                  # mats[0]
           + mesh)
    edit = struct.pack("<HI", 0x4000, 6 + len(obj)) + obj
    three_d = struct.pack("<HI", 0x3D3D, 6 + len(edit)) + edit
    ver = struct.pack("<HI", 0x0002, 10) + struct.pack("<I", 3)
    main = struct.pack("<HI", 0x4D4D,
                       6 + len(ver) + len(three_d)) + ver + three_d
    return main
    body = vchunk + fchunk
    mesh = struct.pack("<HI", 0x4100, 6 + len(body)) + body
    obj = (struct.pack("<H", 0) + b"\0" * 26      # name, 26 bytes, empty
           + struct.pack("<I", 0)                  # nmatids
           + struct.pack("<H", 0)                  # mats[0]
           + mesh)
    edit = struct.pack("<HI", 0x4000, 6 + len(obj)) + obj
    three_d = struct.pack("<HI", 0x3D3D, 6 + len(edit)) + edit
    ver = struct.pack("<HI", 0x0002, 10) + struct.pack("<I", 3)
    main = struct.pack("<HI", 0x4D4D,
                       6 + len(ver) + len(three_d)) + ver + three_d
    return main


def glb(positions=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))) -> bytes:
    """A minimal but real GLB: a JSON chunk and a BIN chunk."""
    pos = b"".join(struct.pack("<fff", *v) for v in positions)
    nrm = struct.pack("<fff", 0.0, 0.0, 1.0) * len(positions)
    idx = struct.pack("<HHH", 0, 1, 2)
    while len(nrm) % 4:
        nrm += b"\0"
    while len(idx) % 4:
        idx += b"\0"
    blob = pos + nrm + idx
    gltf = {
        "asset": {"version": "2.0"},
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1},
                                    "indices": 2, "mode": 4}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions),
             "type": "VEC3"},
            {"bufferView": 1, "componentType": 5126, "count": len(positions),
             "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": 3,
             "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(nrm)},
            {"buffer": 0, "byteOffset": len(pos) + len(nrm), "byteLength": len(idx)},
        ],
        "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    out = b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js
    out += struct.pack("<II", len(blob), 0x004E4942) + blob
    return out


MOVER_GEOMETRY = (
    '<Geometry Model="Base" Name="Base" Position="'
    '{1,0,0,0}{0,1,0,0}{0,0,1,0}{0,0,0,1}">'
    '<Geometry Model="Yoke" Name="Yoke" Position="'
    '{1,0,0,0}{0,1,0,0}{0,0,1,-0.0934}{0,0,0,1}">'
    '<Geometry Model="Body" Name="Body" Position="'
    '{1,0,0,0}{0,1,0,0}{0,0,1,-0.1443}{0,0,0,1}">'
    '<Beam Model="Lens" Name="Lens" BeamAngle="12" FieldAngle="17" '
    'BeamRadius="0.03" LuminousFlux="48120" LampType="LED" BeamType="Spot" '
    'Position="{1,0,0,0}{0,1,0,-0.025}{0,0,1,-0.1}{0,0,0,1}"/>'
    '</Geometry></Geometry></Geometry>')


def manifest() -> dict:
    """A manifest in exactly the shape /api/console/models produces.

    Built through the real server code rather than hand-written as JSON, so
    a change to what the route emits breaks this test instead of quietly
    leaving the fixture testing the old shape.
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import gdtf_geom as G

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<GDTF DataVersion="1.2"><FixtureType Name="TestCo" '
        'Manufacturer="TestCo" FixtureTypeID="T" Model="Digital Twin">'
        # `File` is deliberately the LONG manufacturer name and `Name` the
        # short part name, exactly as the real Intimidator does it, because
        # the only thing connecting a node's Model="Base" to
        # `models/3ds/Chauvet DJ Intimidator Spot 260 Base.3ds` is the
        # <Models> declaration.  A fixture where the two are equal cannot
        # catch a parser that keys by the file stem instead - and that parser
        # downloads every model, parses every model and draws nothing, with
        # no error anywhere.
        '<Models>'
        '<Model File="TestCo Digital Twin Base" Name="Base" Width="0.1495"'
        ' Height="0.089076" Length="0.192916"/>'
        '<Model File="TestCo Digital Twin Yoke" Name="Yoke" Width="0.083"'
        ' Height="0.191638" Length="0.231117"/>'
        '<Model File="TestCo Digital Twin Body" Name="Body" Width="0.265643"'
        ' Height="0.297168" Length="0.322282"/>'
        '<Model File="TestCo Digital Twin Lens" Name="Lens" Width="0.03"'
        ' Height="0.03" Length="0.03"/>'
        '</Models>'
        '<Geometries>' + MOVER_GEOMETRY + '</Geometries>'
        '</FixtureType></GDTF>'
    ).encode("utf-8")

    geom = G.parse_geometry(xml)
    geom["kinematics"] = G.resolve_kinematics(geom, has_pan=True, has_tilt=True)
    built = {
        "id": "test-mover", "ok": True, "reason": "", "source": "test.gdtf",
        "geometry": geom, "models": {}, "emitters": [], "files": {},
        "key": "synthetic", "heads": [17, 20],
    }
    pub = G.public_manifest(built)
    pub["heads"] = [17, 20]
    pub["summary"] = G.summarise(built)
    # The models the frontend would fetch, by the name a NODE refers to.
    # Served as a lookup so the load test can hand real bytes to the real
    # parser.  `Lens` is deliberately absent: the Intimidator's Beam node
    # says Model="Lens" and the profile ships no model for it, because it is
    # the lens rather than a part.  "No such model" is the right answer.
    pub["models"] = {
        "Base": {"name": "models/3ds/TestCo Digital Twin Base.3ds",
                 "ext": ".3ds"},
        "Yoke": {"name": "models/3ds/TestCo Digital Twin Yoke.3ds",
                 "ext": ".3ds"},
        "Body": {"name": "models/3ds/TestCo Digital Twin Body.3ds",
                 "ext": ".3ds"},
    }
    return {"definitions": [pub], "count": 1}


def model_bytes() -> dict:
    """Bytes for the loader tests, base64'd so the harness can carry them.

    `grid` is six faces over a 2x3 vertex grid.  Six, because one face is the
    case a stride bug survives: with a single face there is exactly one flags
    word and skipping it accidentally produces the right answer.  Six faces
    and a distinctive flags word means a 2-byte stride reads 0xBEEF as a
    vertex index and fails loudly instead of quietly.

    `millimetres` reproduces the REAL Intimidator Base.3ds numbers: bounds
    192.916 x 149.500 x 89.076 against a profile declaring 0.192916 x 0.1495
    x 0.089076 metres.  Same three numbers a factor of 1000 apart, which is
    what 3D Studio actually writes, and the reason the renderer fits a mesh
    to the profile's declared size instead of trusting the file's units.
    """
    grid_v = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0),
              (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), (2.0, 1.0, 0.0))
    grid_f = ((0, 1, 4), (0, 4, 3), (1, 2, 5), (1, 5, 4), (3, 4, 5), (4, 5, 2))
    mm_v = ((0.0, 0.0, 0.0), (192.916, 0.0, 0.0), (0.0, 149.5, 0.0),
            (0.0, 0.0, 0.0), (192.916, 0.0, 0.0), (0.0, 149.5, 0.0))
    mm_f = tuple(tuple(f[i] for i in range(3)) for f in grid_f)
    # 712 bytes of trailing slack, the same ratio the real file has, so a
    # parser that insists length == 8 + count*8 is caught here too.
    pad = b"\0" * 712
    return {
        "threeds": base64.b64encode(three_ds()).decode("ascii"),
        "grid": base64.b64encode(three_ds(grid_v, grid_f)).decode("ascii"),
        "padded": base64.b64encode(
            three_ds(grid_v, grid_f, pad)).decode("ascii"),
        "millimetres": base64.b64encode(
            three_ds(mm_v, mm_f)).decode("ascii"),
        "glb": base64.b64encode(glb()).decode("ascii"),
    }


def gdtf_archive() -> bytes:
    """A real .gdtf zip, for the Python-side extraction tests."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("description.xml", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<GDTF DataVersion="1.2"><FixtureType Name="T" Manufacturer="T">'
            '<Models><Model File="Body" Name="Body"/></Models>'
            '<Geometries>' + MOVER_GEOMETRY + '</Geometries>'
            '</FixtureType></GDTF>'))
        z.writestr("models/gltf/Body.glb", glb())
    return buf.getvalue()
