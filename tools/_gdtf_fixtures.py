'''Build synthetic 3DS / GLB bytes and a manifest, for the twin test.'''

import base64
import io
import json
import os
import struct
import zipfile


def three_ds(tri=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))) -> bytes:
    """A minimal 3DS chunk file, in the layout the REAL files use.

    Hand-built so the parser is exercised against bytes we control.  The
    alternative - a real .gdtf out of the operator's library - is gitignored
    data, so a test reading it passes here and fails for everyone else.  The
    truss-bar test did exactly that and was only caught by cloning the
    commit somewhere clean.

    The two chunks are the ones the scan looks for, and each is sized so
    that `8 + count * stride == length` EXACTLY, which is the validity test
    the parser applies:

        0x4110 VERTICES  count * 12 bytes of float3
        0x4120 FACES     count * 8 bytes of (flags, i, j, k)

    The faces carry the 0x4140 id, because that is what the real Intimidator
    .3ds on this machine does, and a parser that only accepts 0x4120 finds
    vertices and no faces on a genuine file.
    """
    verts = tri
    # The count is a uint16, so "<HIH" (8 bytes) and NOT "<HII" (10).  The
    # parser reads the count as u16 at +6, so a 4-byte count puts the real
    # value in the wrong half of the field and every chunk looks empty.
    vchunk = (struct.pack("<HIH", 0x4110, 8 + len(verts) * 12, len(verts))
              + b"".join(struct.pack("<fff", *v) for v in verts))
    faces = [(0, 1, 2)]
    fchunk = (struct.pack("<HIH", 0x4140, 8 + len(faces) * 8, len(faces))
              + b"".join(struct.pack("<4H", 7, *f) for f in faces))
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
        '<Models>'
        '<Model File="Base" Name="Base" Width="0.15" Height="0.09" Length="0.19"/>'
        '<Model File="Yoke" Name="Yoke" Width="0.08" Height="0.19" Length="0.23"/>'
        '<Model File="Body" Name="Body" Width="0.2" Height="0.3" Length="0.32"/>'
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
    # The models the frontend would fetch, by name.  Served as a lookup so
    # the load test can hand real bytes to the real parser.
    pub["models"] = {
        "Base": {"name": "models/gltf/Base.glb", "ext": ".glb"},
        "Yoke": {"name": "models/gltf/Yoke.glb", "ext": ".glb"},
        "Body": {"name": "models/gltf/Body.glb", "ext": ".glb"},
    }
    return {"definitions": [pub], "count": 1}


def model_bytes() -> dict:
    """Bytes for the loader tests, base64'd so the harness can carry them.

    `millimetres` is a 3DS whose vertices are the REAL Intimidator Base.3ds
    numbers: bounds 192.916 x 149.500 x 89.076, against a profile declaring
    0.192916 x 0.1495 x 0.089076 metres.  Same three numbers, a factor of
    1000 apart, which is what 3D Studio actually writes - and the reason the
    renderer fits a mesh to the profile's declared size instead of trusting
    the file's units.  Reproduced here from the measurement so the test does
    not need the operator's gitignored .gdtf to exist.
    """
    tri = ((0.0, 0.0, 0.0), (192.916, 0.0, 0.0), (0.0, 149.5, 0.0))
    return {
        "threeds": base64.b64encode(three_ds()).decode("ascii"),
        "millimetres": base64.b64encode(three_ds(tri)).decode("ascii"),
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
