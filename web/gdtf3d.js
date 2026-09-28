/* GDTF-driven 3D fixture twins.
 *
 * =========================================================================
 * THE RULE THAT SHAPES EVERYTHING HERE
 * =========================================================================
 *
 *                  ┌─────────────┐
 *                  │ Jarvis Core │
 *                  │ Engine      │
 *                  └──────┬──────┘
 *                         │
 *                   authoritative state
 *                         │
 *           ┌────────────┼────────────┐
 *           │            │            │
 *         DMX          Web UI       3D  ← this file
 *         output       controls    renderer
 *
 * This module NEVER computes a DMX value.  It never decides what a head is
 * doing.  It receives what the engine already decided and turns it into
 * transforms, materials and lights.  There is exactly one implementation of
 * "what is head 17 doing" and it is in Python.
 *
 * That is why `setDmx()` takes the engine's numbers verbatim rather than
 * reading a channel: a second calculation here would drift from the frame
 * on the wire, and the operator would be looking at a fixture that does not
 * match the rig.
 *
 * =========================================================================
 * WHY THIS IS SEPARATE FROM viz.js
 * =========================================================================
 *
 * viz.js draws a RIG: a room, truss, beams, a camera.  It has worked for a
 * long time and it keeps working, with its generic primitive bodies.  This
 * module is about a single fixture's PHYSICAL MODEL: the real mesh out of
 * the GDTF, the real hierarchy, the real pivots.
 *
 * The split matters because the two have opposite failure modes.  If a GDTF
 * is missing, malformed, or has no model at all, this module must degrade
 * to nothing and viz.js must carry on exactly as before.  A twin renderer
 * that can take the visualiser down is worse than no twin renderer, because
 * a console you cannot see is a console you cannot trust.
 *
 * So: nothing in here throws past its own boundary, and the caller can
 * always fall back.  `fallback` is a first-class state, not an error.
 *
 * =========================================================================
 * COORDINATE SYSTEMS - the part that is silently wrong everywhere else
 * =========================================================================
 *
 * GDTF is RIGHT-HANDED with +Z UP, in metres, and its matrices are
 * ROW-VECTOR: a point transforms as `v * M`, so translation sits at
 * indices 3, 7 and 11.
 *
 * WebGL is LEFT-HANDED with +Y UP, COLUMN-VECTOR: `M * v`, translation at
 * 12, 13, 14.
 *
 * So there are two conversions and they happen in two different places, on
 * purpose:
 *
 *   1. THE SWAP, ONCE, per node: a node's local GDTF matrix is converted
 *      on the way in - Z-up rows become Y-up columns, and row-vector
 *      becomes column-vector.  After this, everything below is ordinary
 *      WebGL and nobody has to remember which convention a given number is
 *      in.
 *   2. THE SCALE, ONCE, per instance: the visualiser's world is in metres
 *      too, so no scaling is needed.  (An earlier visualiser worked in
 *      centimetres; a 12 m room was 1200 units.  Both worlds are metres
 *      now, and if that ever changes it changes in ONE place.)
 *
 * Reading GDTF's translation from the wrong indices, or applying the swap
 * twice, produces a fixture that rotates about a point nowhere near its own
 * mechanism - and it still rotates, so it looks plausible.  It is the most
 * common way a GDTF viewer is wrong and the hardest to see by eye.
 */
(function (root) {
  "use strict";

  // ======================================================================
  // matrices - WebGL convention, COLUMN-vector, +Y up, metres
  // ======================================================================

  function mIdent() { return [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]; }

  function mMul(a, b) {                    // a * b, column-vector
    const o = new Array(16);
    for (let c = 0; c < 4; c++) {
      for (let r = 0; r < 4; r++) {
        o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1]
                     + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
      }
    }
    return o;
  }

  function mPersp(fovY, aspect, near, far) {
    const f = 1 / Math.tan(fovY / 2), o = new Array(16).fill(0);
    o[0] = f / aspect; o[5] = f; o[11] = -1;
    o[10] = (far + near) / (near - far);
    o[14] = (2 * far * near) / (near - far);
    return o;
  }

  /* ---------------------------------------------------------------------
   * THE PROFILE'S FRAME, AND THE ONE CONVERSION OUT OF IT.
   *
   * The node tree is authored in one frame - column-vector matrices once
   * transposed, +Z up, metres - and the visualiser works in another: +Y up.
   * So the chain is composed entirely in the profile's frame and converted
   * ONCE, at the end.
   *
   * The first version converted each node's local matrix on the way in.  That
   * looks equivalent and is not: the swap is a linear map, and applying it
   * at every level applies it once per level.  Two levels deep it had been
   * applied twice, which mirrored the chain - and pan and tilt still looked
   * right, because a double swap mostly cancels for rotations.  What it broke
   * was the PIVOTS: a head that should hang 237.7 mm below its yoke landed
   * somewhere else entirely, and no test complained, because every check was
   * reading a matrix that had been flipped an even number of times and so
   * came back looking unchanged.
   * --------------------------------------------------------------------- */

  /* Rotations in the profile's frame, written out rather than derived from
   * the WebGL ones, because getting an axis or a sign wrong here is
   * invisible in a still frame and glaring the moment anything moves.
   * These are the profile's own (row-vector) form; the call sites transpose
   * them into the layout everything else here uses. */
  function gRotZ(deg) {                 // about the fixture's UP axis: PAN
    const a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
    return [c, s, 0, 0,  -s, c, 0, 0,  0, 0, 1, 0,  0, 0, 0, 1];
  }
  function gRotX(deg) {                 // about the head's TILT axis
    const a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
    return [1, 0, 0, 0,  0, c, s, 0,  0, -s, c, 0,  0, 0, 0, 1];
  }

  /* A point through a COLUMN-vector matrix: M * p.  (The profile's own
   * row-vector form is transposed on the way in - see gdtfTranspose - so
   * everything after that is ordinary column-vector algebra.) */
  function pointMul(m, p) {
    return [0, 1, 2, 3].map((r) =>
      m[r] * p[0] + m[4 + r] * p[1] + m[8 + r] * p[2] + m[12 + r] * p[3]);
  }

  /* GDTF stores each matrix TRANSPOSED relative to the usual column-major
   * layout: the translation sits at 3, 7 and 11, which is the fourth
   * element of rows 0, 1 and 2 - i.e. of COLUMN 3 read the ordinary way.
   *
   * So a profile matrix is transposed exactly once, on the way in, and from
   * there it is a normal column-vector matrix, still in the profile's own
   * +Z-up frame.  Doing the transpose here rather than at the end is not
   * tidiness, it is correctness: the first version composed the chain in the
   * profile's raw layout using a multiply that assumed the ordinary layout.
   * Translations still came out right, because they only add - and every
   * composed ROTATION came out in the wrong order, so two nodes deep a pan
   * followed by a tilt was really applied tilt-then-pan.  It is invisible on
   * a single axis and wrong on both.
   */
  function gdtfTranspose(m) {
    if (!m || m.length !== 16) return mIdent();
    const o = new Array(16);
    for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) o[c * 4 + r] = m[r * 4 + c];
    return o;
  }

  /* +Z up -> +Y up, in the BASIS and in the TRANSLATION.
   *
   * Both halves are needed.  The first version swapped only the basis, so a
   * node 93.4 mm BELOW its parent - which is how GDTF spells a yoke, since
   * its up axis is Z and the yoke hangs down - was placed 93.4 mm BEHIND it
   * instead.  The hierarchy was right, the pivots were right, and every
   * mover in the rig sat 93 mm off its truss position and partly through the
   * floor.  The translation is a point; the axis swap is a linear map; a
   * point goes through the same map as a direction.
   */
  function swapYZ(m) {
    const o = m.slice();
    for (let k = 0; k < 4; k++) {
      const y = o[4 + k], z = o[8 + k];
      o[4 + k] = z; o[8 + k] = y;
    }
    const ty = o[13], tz = o[14];
    o[13] = tz; o[14] = ty;
    return o;
  }

  /* A GDTF POINT into the visualiser's frame: +Z up becomes +Y up, so the Y
   * and Z components swap.  Nothing else happens - in particular there is no
   * transpose, because a point is not a matrix. */
  function gdtfPoint(p) { return [p[0], p[2], p[1]]; }

  /* GDTF (row-vector, +Z up) -> WebGL (column-vector, +Y up).
   *
   * Transposing turns row-vector into column-vector, which also moves the
   * translation from 3, 7, 11 to 12, 13, 14.  The swap then exchanges the Y
   * and Z AXES in the basis AND in the translation: the translation is a
   * point, and a point goes through the same linear map as a direction.
   *
   * The first version swapped only the basis, so a node 93.4 mm BELOW its
   * parent - which is how GDTF spells a yoke, since its up axis is Z and the
   * yoke hangs down - was placed 93.4 mm BEHIND it instead.  The hierarchy
   * was right, the pivots were right, and every mover in the rig sat 93 mm
   * off its truss position and partly through the floor.
   */
  function gdtfToWebGL(m) {
    return swapYZ(gdtfTranspose(m));
  }

  function mTranslate(x, y, z) {
    const o = mIdent(); o[12] = x; o[13] = y; o[14] = z; return o;
  }

  function mRotX(a) { const o = mIdent(), c = Math.cos(a), s = Math.sin(a);
    o[5] = c; o[6] = s; o[9] = -s; o[10] = c; return o; }
  function mRotY(a) { const o = mIdent(), c = Math.cos(a), s = Math.sin(a);
    o[0] = c; o[2] = -s; o[8] = s; o[10] = c; return o; }
  function mRotZ(a) { const o = mIdent(), c = Math.cos(a), s = Math.sin(a);
    o[0] = c; o[1] = s; o[4] = -s; o[5] = c; return o; }

  function mRotAxis(axis, angle) {
    if (axis === "x") return mRotX(angle);
    if (axis === "y") return mRotY(angle);
    if (axis === "z") return mRotZ(angle);
    return mIdent();
  }

  // ======================================================================
  // the loaders
  // ======================================================================
  /* 3DS is what three of the four real GDTFs on this machine actually ship
   * (`models/3ds/*.3ds`); GLB is what the fourth does (`models/gltf/*.glb`).
   * Both are parsed here, from bytes, with no dependency and no loader
   * object: the visualiser has no glTF loader and adding one for a format
   * that appears in a minority of profiles is the wrong trade.
   *
   * Both return the SAME shape, so nothing downstream knows which it got:
   *
   *   { name, meshes: [{ positions, normals, indices }], bounds }
   *
   * and a failed parse returns `{ name, meshes: [], failed: reason }` -
   * which is a fallback, not an exception.
   */

  /* Normalise whatever we were handed into a real ArrayBuffer.
   *
   * `fetch().arrayBuffer()` in the browser gives one directly, but a
   * Uint8Array view, a Node Buffer and a DataView are all reasonable things
   * for a caller to pass, and all three are TYPED ARRAYS rather than
   * ArrayBuffers - which `new DataView(...)` rejects outright with "First
   * argument to DataView constructor must be an ArrayBuffer".  Slicing
   * respects byteOffset, so a view into the middle of a bigger buffer
   * parses as itself and not as the whole thing. */
  function asBuffer(b) {
    if (!b) return null;
    if (b instanceof ArrayBuffer) return b;
    if (ArrayBuffer.isView(b)) {
      return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
    }
    if (b.buffer instanceof ArrayBuffer) return b.buffer;
    return null;
  }

  /* 3DS is a legacy chunk format, and the files GDTF ships do not agree on
   * how to lay it out.  A Chauvet Intimidator's Base.3ds on this machine
   * has: MAIN -> VERSION, 3D_OBJECT -> MAT_GROUP + EDIT_OBJECT, and inside
   * that a MESH whose first child is VERTICES (0x4110, 856 verts) followed
   * by a 6856-byte faces chunk - which is exactly 8 + 856*8, the FACES
   * layout - but carrying the id 0x4140, not the 0x4120 the structural
   * walk would insist on.  A walk that trusts ids therefore finds vertices
   * and no faces, and the fixture renders as a point cloud.
   *
   * So the scan is by SELF-CONSISTENCY, not by id: a candidate chunk is
   * accepted only when its declared length equals 8 + count*stride for the
   * stride that chunk type uses.  A false positive requires a 4-byte window
   * to satisfy that arithmetic exactly, which is not something that happens
   * by accident - whereas trusting ids is what just failed.
   *
   * Nothing is executed and nothing is allocated from an unvalidated count:
   * every accepted count is bounded by the chunk's own declared length,
   * which was itself checked against the file size. */
  function parse3DS(buf, name) {
    buf = asBuffer(buf);
    if (!buf) return { name, meshes: [], failed: "no bytes" };
    const dv = new DataView(buf);
    if (buf.byteLength < 8) return { name, meshes: [], failed: "too short" };
    const id = dv.getUint16(0, true);
    if (id !== 0x4D4D && id !== 0x4D3D) {
      return { name, meshes: [], failed: "not a 3DS chunk file" };
    }
    const LE = id === 0x4D4D;
    const u16 = (o) => dv.getUint16(o, LE), u32 = (o) => dv.getUint32(o, LE);
    const f32 = (o) => dv.getFloat32(o, LE);

    let verts = null, faces = null;
    for (let o = 0; o + 8 <= buf.byteLength; o++) {
      const clen = u32(o + 2);
      if (clen < 8 || o + clen > buf.byteLength) continue;
      const cid = u16(o);
      const n = u16(o + 6);
      if (cid === 0x4110 && 8 + n * 12 === clen && !verts) {
        const v = new Float32Array(n * 3);
        for (let i = 0; i < n; i++) {
          v[i * 3] = f32(o + 8 + i * 12);
          v[i * 3 + 1] = f32(o + 8 + i * 12 + 4);
          v[i * 3 + 2] = f32(o + 8 + i * 12 + 8);
        }
        verts = v;
      } else if ((cid === 0x4120 || cid === 0x4140) && 8 + n * 8 === clen
                 && !faces && verts) {
        // Paired with a vertex array that has already been found, and its
        // indices are range-checked below - that is what stops an unrelated
        // chunk which merely happens to be 8 + n*8 long from being read as
        // faces.  The count is NOT required to equal the vertex count: most
        // exporters do emit one face per vertex, but not all of them, and
        // insisting on it rejects a perfectly good mesh.
        const f = new Uint16Array(n * 3);
        for (let i = 0; i < n * 3; i++) f[i] = u16(o + 10 + i * 2);
        faces = f;
      }
    }
    if (!verts) return { name, meshes: [], failed: "no vertex chunk" };
    if (!faces) return { name, meshes: [], failed: "vertices but no faces" };
    // Every index must name a real vertex.  A 3DS from the wild can pair
    // faces with a vertex array we did not expect, and a triangle naming
    // vertex 100000 of a 40-vertex mesh reads off the end of a typed array
    // and hangs the tab rather than failing.
    const nv = verts.length / 3;
    for (let i = 0; i < faces.length; i++) {
      if (faces[i] >= nv) {
        return { name, meshes: [],
                 failed: "face index %d of %d vertices" % (faces[i], nv) };
      }
    }
    const meshes = [{ positions: verts, normals: faceNormals(verts, faces),
                      indices: faces }];
    return { name, meshes, bounds: boundsOf(meshes), format: "3ds",
             vertices: verts.length / 3, faces: faces.length / 3 };
  }

  function faceNormals(pos, idx) {
    const n = new Float32Array(pos.length);
    for (let i = 0; i < idx.length; i += 3) {
      const a = idx[i]*3, b = idx[i+1]*3, c = idx[i+2]*3;
      const ux = pos[b]-pos[a], uy = pos[b+1]-pos[a+1], uz = pos[b+2]-pos[a+2];
      const vx = pos[c]-pos[a], vy = pos[c+1]-pos[a+1], vz = pos[c+2]-pos[a+2];
      const nx = uy*vz - uz*vy, ny = uz*vx - ux*vz, nz = ux*vy - uy*vx;
      for (const o of [a, b, c]) { n[o]+=nx; n[o+1]+=ny; n[o+2]+=nz; }
    }
    for (let i = 0; i < n.length; i += 3) {
      const l = Math.hypot(n[i], n[i+1], n[i+2]) || 1;
      n[i] /= l; n[i+1] /= l; n[i+2] /= l;
    }
    return n;
  }

  /* GLB: a 12-byte header then a sequence of typed chunks, the first of
   * which is the glTF JSON.  Only POSITION and NORMAL accessors are read -
   * indices, textures and materials are a renderer concern and the
   * visualiser has no texture stage. */
  function parseGLB(buf, name) {
    buf = asBuffer(buf);
    if (!buf) return { name, meshes: [], failed: "no bytes" };
    const dv = new DataView(buf);
    if (buf.byteLength < 12 || dv.getUint32(0, true) !== 0x46546C67) {
      return { name, meshes: [], failed: "not a GLB" };
    }
    let o = 12, json = null, bin = null;
    while (o + 8 <= buf.byteLength) {
      const len = dv.getUint32(o, true), type = dv.getUint32(o + 4, true);
      // A VIEW, not a slice.  ArrayBuffer.slice() returns another
      // ArrayBuffer, which has no .buffer and no .byteOffset, so every
      // accessor read below silently produced a zero-length array and every
      // GLB model came back empty - a failure that looks like a broken
      // model file rather than a wrong type.
      const body = new Uint8Array(buf, o + 8, Math.min(len, buf.byteLength - o - 8));
      if (type === 0x4E4F534A) json = JSON.parse(new TextDecoder().decode(body));
      else if (type === 0x004E4942) bin = body;
      // Chunk lengths are already 4-byte padded in a well-formed GLB, and we
      // must not skip past a short final chunk to find the end of the file.
      o += 8 + len;
    }
    if (!json) return { name, meshes: [], failed: "no JSON chunk" };
    const meshes = [];
    for (const m of (json.meshes || [])) {
      for (const prim of (m.primitives || [])) {
        const pos = readAccessor(json, bin, prim.attributes.POSITION);
        if (!pos) continue;
        const nrm = readAccessor(json, bin, prim.attributes.NORMAL);
        const idxRaw = readAccessor(json, bin, prim.indices);
        const indices = idxRaw
          ? (idxRaw instanceof Uint32Array ? idxRaw : new Uint32Array(idxRaw))
          : null;
        const n = pos.length / 3;
        const idx = indices || new Uint32Array(n);
        if (!nrm) {
          meshes.push({ positions: pos, normals: faceNormals(pos, idx),
                        indices: idx });
        } else if (idxRaw && !(idxRaw instanceof Uint32Array)) {
          meshes.push({ positions: pos, normals: nrm,
                        indices: new Uint32Array(idxRaw) });
        } else {
          meshes.push({ positions: pos, normals: nrm, indices: idx });
        }
      }
    }
    if (!meshes.length) return { name, meshes: [], failed: "no primitives" };
    return { name, meshes, bounds: boundsOf(meshes), format: "glb" };
  }

  const _CT = { 5120: Int8Array, 5121: Uint8Array, 5122: Int16Array,
                5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array };
  /* glTF component COUNTS, keyed by the accessor's TYPE - not by its
   * componentType.  Keying it by componentType (5126 and friends) is the
   * mistake the first version made: every lookup missed, the default of 3
   * happened to be right for the vertex data and wrong for a scalar index,
   * so the index read ran off the end of the buffer, returned null, and the
   * mesh came back with generated indices. */
  const _NC = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT2: 4, MAT3: 9,
                MAT4: 16 };

  function readAccessor(json, bin, idx) {
    if (idx == null || !json || !json.accessors) return null;
    const acc = json.accessors[idx];
    if (!acc || !json.bufferViews) return null;
    const bv = json.bufferViews[acc.bufferView];
    if (!bv || !bin) return null;
    const nc = _NC[acc.type] || 3;
    const Ctor = _CT[acc.componentType];
    if (!Ctor) return null;
    const elem = Ctor.BYTES_PER_ELEMENT || 1;
    const start = (bv.byteOffset || 0) + (acc.byteOffset || 0);
    const n = acc.count * nc;
    // Both bounds matter: a truncated file must not produce a partial
    // buffer that WebGL then reads past the end of.
    if (start < 0 || start + n * elem > bin.byteLength) return null;
    return new Ctor(bin.buffer, bin.byteOffset + start, acc.count * nc);
  }

  function boundsOf(meshes) {
    const lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    for (const m of meshes) {
      const p = m.positions;
      for (let i = 0; i < p.length; i += 3) {
        for (let k = 0; k < 3; k++) {
          if (p[i+k] < lo[k]) lo[k] = p[i+k];
          if (p[i+k] > hi[k]) hi[k] = p[i+k];
        }
      }
    }
    const c = [(lo[0]+hi[0])/2, (lo[1]+hi[1])/2, (lo[2]+hi[2])/2];
    return { min: lo, max: hi, centre: c,
             size: [hi[0]-lo[0], hi[1]-lo[1], hi[2]-lo[2]],
             radius: Math.hypot(hi[0]-lo[0], hi[1]-lo[1], hi[2]-lo[2]) / 2 };
  }

  function parseModel(buf, name, ext) {
    try {
      if (ext === ".glb" || ext === ".gltf") return parseGLB(buf, name);
      if (ext === ".3ds") return parse3DS(buf, name);
      if (ext === ".stl") return parseSTL(buf, name);
      if (ext === ".obj") return parseOBJ(buf, name);
    } catch (err) {
      return { name, meshes: [], failed: String((err && err.message) || err) };
    }
    return { name, meshes: [], failed: "unsupported format " + ext };
  }

  function parseSTL(buf, name) {
    buf = asBuffer(buf);
    if (!buf) return { name, meshes: [], failed: "no bytes" };
    const dv = new DataView(buf);
    if (buf.byteLength < 84) return { name, meshes: [], failed: "too short" };
    const n = dv.getUint32(80, true);
    if (84 + n * 50 > buf.byteLength) {
      return { name, meshes: [], failed: "truncated binary STL" };
    }
    const pos = new Float32Array(n * 9);
    for (let i = 0; i < n; i++) {
      const o = 84 + i * 50 + 12;      // skip the facet normal
      for (let k = 0; k < 9; k++) pos[i*9+k] = dv.getFloat32(o + k*4, true);
    }
    const idx = new Uint32Array(n * 3);
    for (let i = 0; i < n * 3; i++) idx[i] = i;
    const meshes = [{ positions: pos, normals: faceNormals(pos, idx),
                      indices: idx }];
    return { name, meshes, bounds: boundsOf(meshes), format: "stl" };
  }

  function parseOBJ(text, name) {
    if (typeof text !== "string") {
      const b = asBuffer(text);
      if (!b) return { name, meshes: [], failed: "no bytes" };
      text = new TextDecoder().decode(b);
    }
    const verts = [], idx = [];
    for (const line of text.split("\n")) {
      if (line[0] === "v") {
        const p = line.trim().split(/\s+/);
        verts.push(+p[1], +p[2], +p[3]);
      } else if (line[0] === "f") {
        const p = line.trim().split(/\s+/);
        for (let i = 1; i < p.length; i++) {
          idx.push(parseInt(p[i], 10) - 1);
        }
      }
    }
    if (!verts.length) return { name, meshes: [], failed: "no vertices" };
    const pos = new Float32Array(verts);
    const ind = new Uint32Array(idx);
    const meshes = [{ positions: pos, normals: faceNormals(pos, ind),
                      indices: ind }];
    return { name, meshes, bounds: boundsOf(meshes), format: "obj" };
  }

  // ======================================================================
  // DEFINITION - shared, immutable, one per fixture TYPE
  // ======================================================================

  function Definition(manifest) {
    this.id = manifest.id || "";
    this.meshes = {};                          // stem -> parsed, by loadModels
    this.refs = 0;                             // live instances (refcount)
    this.pending = null;                       // in-flight load, for dedup
    this.failed = {};                          // stem -> why it would not load
    this._byPath = null;                       // derived indexes, built lazily
    this._roots = null;
    this._beamPaths = null;
    this.scale = 1;                            // mesh -> profile, see fitModels
    this._fitted = false;
    this.update(manifest);
  }

  /* Refresh the DESCRIPTIVE half of a definition without touching the
   * runtime half.
   *
   * The split exists because the two halves have different lifetimes.  The
   * description comes off the server and can arrive again at any time; the
   * meshes, the refcount and the in-flight load are expensive and slow, and
   * they are the whole reason instances share a definition at all.  A
   * re-import therefore must NOT drop four downloaded meshes and start the
   * fetch over - so it updates the prose and leaves the resources alone.
   */
  Definition.prototype.update = function (manifest) {
    this.ok = !!manifest.ok;
    this.reason = manifest.reason || "";
    this.source = manifest.source || "";
    this.nodes = manifest.nodes || [];        // the GDTF tree
    this.kinematics = manifest.kinematics || {};
    this.beams = manifest.beams || [];
    this.models = manifest.models || {};
    this.modelMeta = manifest.modelMeta || {};
    this.emitters = manifest.emitters || [];
    this._byPath = null;                       // derived, so it must be dropped
    this._roots = null;
    this._beamPaths = null;
    this._fitted = false;                      // the new size needs fitting
    this.refreshState();
    return this;
  };

  /* The fallback state, derived rather than remembered.
   *
   *   geometry   the real model, from the real GDTF
   *   primitives the real hierarchy and pivots, but no usable mesh
   *   fallback   no geometry at all: a generic fixture
   *
   * Derived every time rather than assigned at each site, because it has
   * three inputs (the manifest, the tree, what actually loaded) and any one
   * of them can change the answer.  A state cached at parse time is a state
   * that lies after a failed load - which is exactly when the operator most
   * needs it to be honest.
   */
  Definition.prototype.refreshState = function () {
    const stems = Object.keys(this.models || {});
    const loaded = stems.filter(
      (s) => this.meshes[s] && this.meshes[s].meshes && this.meshes[s].meshes.length);
    const all = stems.length > 0 && loaded.length === stems.length;
    if (this.nodes.length) this.state = all ? "geometry" : "primitives";
    else this.state = all ? "primitives" : "fallback";
    return this.state;
  };

  /* Flatten the GDTF tree once, converting every local matrix to WebGL on
   * the way in.  Doing it here means nothing downstream has to remember
   * which convention a number is in, and a node's path is stable. */
  Definition.prototype.index = function () {
    if (this._byPath) return this._byPath;
    const byPath = {};
    const beamPaths = [];
    const walk = (node, path, parent) => {
      const rec = {
        path, name: node.name, kind: node.kind, model: node.model,
        parent: parent ? parent.path : null,
        // Transposed on the way in, so this is a normal column-vector matrix
        // in the profile's own +Z-up frame.  Composing the chain in the raw
        // layout instead is the mistake documented above gdtfTranspose:
        // translations still add up, so they look right, and the rotations
        // silently come out in the wrong order.
        local: gdtfTranspose(
          (node.matrix && node.matrix.length === 16) ? node.matrix : null),
        children: [],
      };
      rec.localGL = swapYZ(rec.local);
      byPath[path] = rec;
      if (node.kind === "beam") {
        beamPaths.push(path);
        rec.beam = node;            // the beam's own geometry and output
      }
      (node.children || []).forEach((c, i) => {
        const cr = walk(c, path + "/" + i, rec);
        rec.children.push(cr.path);
      });
      return rec;
    };
    const roots = [];
    (this.nodes || []).forEach((n, i) => {
      const r = walk(n, String(i), null);
      roots.push(r.path);
    });
    this._byPath = byPath;
    this._roots = roots;
    this._beamPaths = beamPaths;
    return byPath;
  };

  /* Where the light actually leaves the fixture.
   *
   * Taken from the BEAM NODE's own world matrix, not the fixture's origin and
   * not the manifest's flat beam list: on the Intimidator the lens sits
   * 25 mm across and 100 mm forward of the head, inside a yoke that has
   * turned.  A cone started at the middle of the fixture looks fine until
   * the fixture tilts, and then it is visibly coming out of the yoke.
   */
  Definition.prototype.beamNodes = function () {
    this.index();
    return (this._beamPaths || []).map((p) => this._byPath[p]);
  };

  Definition.prototype.node = function (path) {
    return this.index()[path] || null;
  };

  /* The world matrix of every node, in the PROFILE's own frame: column-vector
   * algebra, +Z up, metres.
   *
   * The rotations are inserted AT the node they belong to and inherited by
   * its children, which is the whole point: the head's beam has to follow
   * the head, and the head has to follow the yoke.  One rotation applied to
   * the fixture root would move everything at once and look plausible.
   *
   * gRotZ and gRotX are the PROFILE's rotations, transposed into the layout
   * everything else here uses and written out deliberately rather than
   * reused from the WebGL ones.  A wrong axis or sign is invisible in a
   * still frame and glaring the moment anything moves. */
  Definition.prototype.solveGDTF = function (dmx) {
    const byPath = this.index();
    const out = {};
    const panPath = this.kinematics.pan;
    const tiltPath = this.kinematics.tilt;
    const pan = (dmx && typeof dmx.panDeg === "number") ? dmx.panDeg : 0;
    const tilt = (dmx && typeof dmx.tiltDeg === "number") ? dmx.tiltDeg : 0;
    // Pan is about the fixture's own up axis, which in the profile's frame
    // is Z.  Tilt is about the head's transverse axis.  A fixture with no
    // pan node simply gets no pan, and its matrix is untouched - the solver
    // is never asked to rotate a part the profile does not say can rotate.
    const panRot = panPath ? gdtfTranspose(gRotZ(pan)) : null;
    const tiltRot = tiltPath ? gdtfTranspose(gRotX(tilt)) : null;

    const walk = (path, parentWorld) => {
      const rec = byPath[path];
      if (!rec) return;
      let local = rec.local;
      if (path === panPath && panRot) local = mMul(panRot, local);
      if (path === tiltPath && tiltRot) local = mMul(tiltRot, local);
      const world = parentWorld ? mMul(parentWorld, local) : local;
      out[path] = world;
      for (const c of rec.children) walk(c, world);
    };
    for (const r of this._roots) walk(r, null);
    return out;
  };

  /* The same chain in the visualiser's frame.  swapYZ is applied HERE and
   * only here: once per composed matrix, at the boundary, after the whole
   * hierarchy has been resolved.  Applying it per node instead applies it
   * once per level, which mirrors the chain and puts every pivot in the
   * wrong place while leaving pan and tilt looking correct. */
  Definition.prototype.solve = function (dmx) {
    const raw = this.solveGDTF(dmx);
    const out = {};
    for (const k in raw) out[k] = swapYZ(raw[k]);
    return out;
  };

  /* Rescale a mesh so it matches the size the PROFILE declares.
   *
   * The profile is the authority on how big a fixture is; the mesh is not,
   * because exporters do not agree on units.  Measured on this machine: the
   * Chauvet Intimidator's Base.3ds parses to bounds of
   * 192.916 x 149.500 x 89.076, and its description.xml says
   * Length="0.192916" Width="0.1495" Height="0.089076" metres.  Same three
   * numbers, a factor of 1000 apart - 3D Studio wrote millimetres.  Left
   * alone, every mover in the rig is drawn a kilometre across, which reads
   * as "the visualiser is broken" rather than "the mesh is in mm".
   *
   * A UNIFORM scale from the largest declared dimension, not per-axis: a
   * per-axis fit would silently correct a real modelling error by squashing
   * the fixture to fit, and a squashed mover is a much harder thing to spot
   * than a wrong-sized one.  Uniform scaling preserves the shape and lets a
   * genuine size error stay visible. */
  function fitToProfile(mesh, model) {
    if (!mesh || !mesh.bounds || !model) return 1;
    const declared = [model.width, model.length, model.height]
      .map((v) => (typeof v === "number" && isFinite(v) && v > 0) ? v : 0);
    const want = Math.max.apply(null, declared);
    if (!want) return 1;
    const got = Math.max.apply(null, mesh.bounds.size);
    if (!got) return 1;
    const k = want / got;
    if (!isFinite(k) || k <= 0 || k > 1e4 || k < 1e-4) return 1;
    if (Math.abs(k - 1) < 1e-9) return 1;
    // A factor very close to a power of ten is a UNIT MISMATCH, not a
    // disagreement: exporters write mm, cm and m, and those are the factors
    // that occur.  Snap to the round number so the mesh ends up at exactly
    // the declared size and a 1000.0000004x no longer leaves it 0.4 microns
    // out.  Anything else is left alone - see below.
    const p = Math.pow(10, Math.round(Math.log10(k)));
    const use = (p > 0 && Math.abs(k / p - 1) < 0.02) ? p : k;
    const scale = (arr) => {
      const out = new arr.constructor(arr.length);
      for (let i = 0; i < arr.length; i++) out[i] = arr[i] * use;
      return out;
    };
    for (const m of mesh.meshes) {
      m.positions = scale(m.positions);
      m.normals = scale(m.normals);        // uniform, so still unit length
    }
    mesh.bounds = boundsOf(mesh.meshes);
    mesh.scaled = use;
    // Recorded, because a factor that is NOT a power of ten means the model
    // and the profile genuinely disagree, and the operator deserves to be
    // told rather than handed a quietly-resized fixture.  A refusal would be
    // worse: it leaves a 25x-wrong fixture on screen, which is harder to
    // diagnose than a correct one with a note attached.
    mesh.snapped = (use !== k);        // we rounded to a round unit factor
    return use;
  }

  /* The size the profile declares for one model, in metres. */
  function declaredSize(def, modelName) {
    const m = (def && def.modelMeta && def.modelMeta[modelName]) || null;
    if (!m) return null;
    return { width: +m.width || 0, height: +m.height || 0,
             length: +m.length || 0 };
  }

  /* Fit every loaded mesh of a definition to the profile.  Once, at load. */
  Definition.prototype.fitModels = function () {
    if (this._fitted) return this.scale || 1;
    const scales = [];
    for (const stem of Object.keys(this.meshes || {})) {
      const size = declaredSize(this, stem);
      if (size) scales.push(fitToProfile(this.meshes[stem], size));
    }
    this.scale = scales.length ? scales[0] : 1;
    this._fitted = true;
    return this.scale;
  };

  // ======================================================================
  // INSTANCE - per head, mutable, shares its definition
  // ======================================================================

  function Instance(head, definition) {
    this.head = head.head_no != null ? head.head_no : head.n;
    this.def = definition;
    definition.refs++;                        // the shared-asset refcount
    this.position = [+(head.x || 0), +(head.y || 0), +(head.z || 0)];
    this.kind = head.kind || "floor";
    this.selected = false;
    this.dmx = {};
    this.fallback = definition.state !== "geometry";
    this.fallbackReason = definition.reason || (
      definition.state === "primitives"
        ? "profile has geometry but no model file - drawing primitives"
        : "no geometry in this profile - drawing a generic fixture");
    this.world = null;                        // per-frame, not stored
  }

  Instance.prototype.setDmx = function (dmx) {
    // The ENGINE's numbers, verbatim.  Nothing is derived here.
    this.dmx = dmx || {};
    return this;
  };

  Instance.prototype.setPosition = function (x, y, z) {
    this.position = [+x || 0, +y || 0, +z || 0];
    return this;
  };

  Instance.prototype.dispose = function () {
    if (this.def) { this.def.refs -= 1; this.def = null; }
  };

  // ======================================================================
  // SCENE - the abstraction viz.js talks to, kept clear of the GL calls
  // ======================================================================

  function Scene() {
    this.instances = new Map();       // head_no -> Instance
    this.defs = new Map();           // definition id -> Definition
    this.defsByHead = new Map();     // head_no -> definition id
    this.status = { loading: 0, ready: 0, failed: 0, models: 0, tris: 0 };
  }

  Scene.prototype.definitionFor = function (id) {
    let d = this.defs.get(id);
    if (!d) { d = new Definition({ id, ok: false, reason: "not described" });
              this.defs.set(id, d); }
    return d;
  };

  /* Add every head in a manifest.  The manifest is per DEFINITION, not per
   * head, so fifty PARs cost one Definition and fifty Instances - which is
   * the whole of "one definition, one load, many instances". */
  Scene.prototype.addManifest = function (manifest) {
    const def = this.definitionFor(manifest.id);
    // Refresh the description IN PLACE.  The first version built a whole new
    // Definition and Object.assign'd it over the old one, which also
    // assigned `meshes: {}` and `refs: 0` - so every manifest refresh
    // silently threw away every downloaded mesh, reset the refcount to zero
    // while instances were still alive, and re-fetched all of it.  That is
    // the bug that makes model loading look like a memory leak.
    def.update(manifest);
    (manifest.heads || []).forEach((head) => {
      const h = typeof head === "number" ? { head_no: head } : head;
      const existing = this.instances.get(
        h.head_no != null ? h.head_no : h.n);
      if (existing) {
        existing.fallback = def.state !== "geometry";
        existing.fallbackReason = fallbackReason(def);
        return;                                   // keep the live instance
      }
      const inst = new Instance(h, def);
      this.instances.set(inst.head, inst);
      this.defsByHead.set(inst.head, def.id);
    });
    return this;
  };

  function fallbackReason(def) {
    if (def.state === "primitives") {
      const bad = Object.keys(def.failed || {});
      return def.nodes.length
        ? ("geometry loaded, but " + (bad.length
            ? bad.length + " model(s) would not load: " + bad.join(", ")
            : "the profile ships no model file") + " - drawing primitives")
        : "no geometry in this profile - drawing a generic fixture";
    }
    return def.reason || "no geometry in this profile - drawing a generic fixture";
  }

  Scene.prototype.add = function (head, definitionId) {
    const def = this.definitionFor(definitionId);
    const old = this.instances.get(head.head_no);
    if (old) old.dispose();
    const inst = new Instance(head, def);
    this.instances.set(inst.head, inst);
    this.defsByHead.set(inst.head, def.id);
    return inst;
  };

  Scene.prototype.remove = function (headNo) {
    const inst = this.instances.get(headNo);
    if (inst) inst.dispose();
    this.instances.delete(headNo);
    this.defsByHead.delete(headNo);
    return this;
  };

  Scene.prototype.get = function (headNo) { return this.instances.get(headNo) || null; };

  Scene.prototype.setDmx = function (headNo, dmx) {
    const i = this.instances.get(headNo);
    return i ? i.setDmx(dmx) : null;
  };

  Scene.prototype.setSelected = function (selected) {
    const set = new Set((selected || []).map(Number));
    this.instances.forEach((i) => { i.selected = set.has(i.head); });
    return this;
  };

  /* Per-frame: the world matrix of every node of every instance, composed
   * down the chain.  Cheap because it is only arithmetic on 4x4s and the
   * node trees are tiny; the EXPENSIVE part - parsing, buffers, textures -
   * happens once per definition in loadModels(). */
  Scene.prototype.update = function () {
    const worlds = new Map();
    this.instances.forEach((inst) => {
      const gdtf = inst.def.solveGDTF(inst.dmx);
      const base = mTranslate(inst.position[0], inst.position[1],
                              inst.position[2]);
      const perNode = {};
      for (const path in gdtf) perNode[path] = mMul(base, swapYZ(gdtf[path]));
      inst.world = base;
      inst.nodeWorld = perNode;      // the visualiser's frame, for drawing
      inst.gdtfWorld = gdtf;          // the profile's own frame, for geometry
      worlds.set(inst.head, perNode);
    });
    return worlds;
  };

  /* The beam's world position and direction, which is where the cone
   * actually starts.  Derived from the BEAM NODE's matrix, so the beam
   * leaves the lens rather than the middle of the fixture. */
  Scene.prototype.beamFor = function (headNo) {
    const inst = this.instances.get(headNo);
    if (!inst || !inst.gdtfWorld) return null;
    const nodes = inst.def.beamNodes();
    if (!nodes.length) return null;
    const rec = nodes[0];
    const g = inst.gdtfWorld[rec.path];
    if (!g) return null;
    // DERIVED, not read off a matrix column.  Two points go through the
    // chain and the answer is the difference, so nothing depends on which
    // column holds what.
    //
    // Reading a column was wrong twice over: the first version took column 2
    // and pointed every beam at the floor, and the fix to column 1 was also
    // wrong, because the swap that makes the frame +Y up has by then MOVED
    // the data out of column 1 and into column 2.  Two plausible-looking
    // column indices and neither was right.
    const here = pointMul(g, [0, 0, 0, 1]);
    const there = pointMul(g, [0, -1, 0, 1]);      // the profile faces -Y
    const origin = gdtfPoint(here);
    let d = gdtfPoint([there[0] - here[0], there[1] - here[1],
                       there[2] - here[2]]);
    const len = Math.hypot(d[0], d[1], d[2]) || 1;
    d = [d[0] / len, d[1] / len, d[2] / len];
    const b = rec.beam || {};
    return {
      path: rec.path, origin, dir: d,
      beamAngle: b.beam_angle || 0, fieldAngle: b.field_angle || 0,
      beamRadius: b.beam_radius || 0, flux: b.luminous_flux || 0,
      beamType: b.beam_type || "", name: rec.name,
    };
  };

  Scene.prototype.stats = function () {
    let tris = 0, models = 0, fallback = 0;
    this.defs.forEach((d) => {
      Object.keys(d.models).forEach((stem) => {
        const m = d.meshes[stem];
        if (m) { models++; m.meshes.forEach((x) => { tris += x.indices.length / 3; }); }
      });
    });
    this.instances.forEach((i) => { if (i.fallback) fallback++; });
    return { definitions: this.defs.size, instances: this.instances.size,
             models, tris: Math.round(tris), fallback,
             sharing: this.instances.size > this.defs.size };
  };

  Scene.prototype.dispose = function () {
    this.instances.forEach((i) => i.dispose());
    this.instances.clear();
    this.defsByHead.clear();
    this.defs.clear();
    return this;
  };

  // ======================================================================
  // async model loading, deduped per definition
  // ======================================================================

  /* Fetch and parse every model of a definition, ONCE.
   *
   * The dedup is the important part: `def.pending` holds the in-flight
   * promise, so a second call while the first is still running returns the
   * SAME promise instead of issuing a second set of requests.  Without it a
   * 200-head rig re-fetches on every manifest refresh, which is the bug that
   * makes model loading look like a leak. */
  Scene.prototype.loadModels = function (defId, fetchBytes) {
    const def = this.defs.get(defId);
    if (!def) return Promise.resolve(null);
    if (def.pending) return def.pending;             // dedup
    const stems = Object.keys(def.models || {});
    if (!stems.length) {
      def.state = def.nodes.length ? "primitives" : "fallback";
      return Promise.resolve(def);
    }
    def.pending = Promise.all(stems.map((stem) => {
      const entry = def.models[stem];
      return Promise.resolve()
        .then(() => fetchBytes(def.id, entry))
        .then((bytes) => {
          if (!bytes || !bytes.byteLength) throw new Error("empty response");
          const mesh = parseModel(bytes, stem, entry.ext || "");
          if (mesh.failed || !mesh.meshes.length) {
            throw new Error(mesh.failed || "no triangles");
          }
          def.meshes[stem] = mesh;
        })
        .catch((err) => {
          // A model that will not load is a FALLBACK for that node only.
          // The hierarchy, the pivots and the beam are all still good, so
          // the fixture keeps working - it just has a box where the mesh
          // was.  One model's failure must not cost the other three, which
          // is why the catch is inside the per-stem promise and not around
          // the Promise.all.
          def.failed[stem] = String((err && err.message) || err);
        });
    })).then(() => {
      def.fitModels();          // the profile's declared size is the authority
      def.refreshState();
      def.pending = null;
      this.status.ready++;
      this.status.models += Object.keys(def.meshes).length;
      this.instances.forEach((i) => {
        if (i.def === def) {
          i.fallback = def.state !== "geometry";
          i.fallbackReason = fallbackReason(def);
        }
      });
      return def;
    });
    return def.pending;
  };

  root.GDTF3D = {
    // matrices, exported so the suite can run them under node
    mIdent, mMul, mPersp, mTranslate, mRotX, mRotY, mRotZ, mRotAxis,
    gRotX, gRotZ, pointMul, gdtfPoint, gdtfTranspose, swapYZ, gdtfToWebGL,
    boundsOf, faceNormals, fitToProfile,
    // loaders
    parseGLB, parse3DS, parseSTL, parseOBJ, parseModel, asBuffer,
    // model
    Definition, Instance, Scene,
  };
})(typeof window !== "undefined" ? window : globalThis);
