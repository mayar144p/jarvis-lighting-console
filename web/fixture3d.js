/* Procedural 3D fixture bodies.
 *
 * ==========================================================================
 * WHY THIS EXISTS
 * ==========================================================================
 *
 * Until now a fixture body was a 2D canvas sprite: a `fillRect` and an
 * `ellipse` drawn in the projected frame, shaped by which channel roles the
 * fixture has.  A moving head was a box with a circle on it.  That is fine
 * for a schematic and terrible next to a real GDTF model, because the two
 * are in different visual languages - one flat, one shaded - and the flat
 * one looks like a bug the moment a real model appears beside it.
 *
 * So every fixture gets real 3D geometry.  Two sources, one renderer:
 *
 *   GDTF model, when the profile ships one     -> the actual product
 *   generated here, when it does not           -> a credible stand-in
 *
 * The generated body is driven by the CHANNEL ROLES, which is the same
 * information `bodyFor` used, so a head with pan and tilt gets a yoke and a
 * head that can physically rotate, and a PAR gets a housing.  It is not a
 * picture of a fixture; it is a fixture built out of the parts the profile
 * says the fixture has.
 *
 * ==========================================================================
 * PROPORTIONS
 * ==========================================================================
 *
 * Real, in metres, because a body that is the wrong size is worse than no
 * body at all - it sits in the scene pretending.  A PAR can is about
 * 130 mm across and 300 mm tall; a moving head is about 150 mm wide and
 * 500 mm from the truss to the lens; a batten is long and thin.  These come
 * from typical fixture families, and where a GDTF profile declares a real
 * size the twin's model is used instead of this, so these only have to be
 * right for fixtures with no model file.
 *
 * ==========================================================================
 * WHY NOT INSTANCING
 * ==========================================================================
 *
 * Not yet.  A rig is tens to hundreds of fixtures and each generated body is
 * a few hundred triangles, so the whole set is well inside a normal
 * draw-call budget, and a generated body is chosen by role - so there are
 * perhaps a dozen distinct shapes to cache.  Instancing earns its keep at
 * thousands of instances; adding the indirection now would be complexity
 * paid for in advance and measured later.  The GPU buffers ARE already
 * shared per shape, which is the part that actually mattered.
 */
(function (root) {
  "use strict";

  const TAU = Math.PI * 2;

  /* ------------------------------------------------------------- shapes */
  /* A shape is { positions, normals, indices, parts, size }.
   *
   * `parts` names the movable groups and where they pivot, so the DMX
   * solver can rotate a yoke and a head independently - the same job the
   * GDTF hierarchy does with real matrices, done here with a transform at
   * draw time.  Keeping the two cases shaped alike is deliberate: the
   * renderer asks "give me the world matrix of part X" and does not care
   * whether the answer came from a profile or from this file.
   */

  function mesh() { return { p: [], n: [], i: [], parts: {} }; }

  function push(m, x, y, z, nx, ny, nz) {
    m.p.push(x, y, z);
    m.n.push(nx, ny, nz);
    return m.p.length / 3 - 1;
  }

  function quad(m, a, b, c, d) { m.i.push(a, b, c, a, c, d); }

  /* A box, flat-shaded: six quads with their own normals.  Flat is right
   * here - smooth-shaded boxes look like pillows. */
  function box(m, cx, cy, cz, hx, hy, hz, part) {
    // An INDEX range, not a vertex range: this is what the renderer hands
    // straight to drawElements, and mixing the two units is how a part ends
    // up slicing the wrong triangles.
    const iFrom = m.i.length;
    const F = [
      [[1, 0, 0], [[hx, -hy, -hz], [hx, -hy, hz], [hx, hy, hz], [hx, hy, -hz]]],
      [[-1, 0, 0], [[-hx, -hy, hz], [-hx, -hy, -hz], [-hx, hy, -hz], [-hx, hy, hz]]],
      [[0, 1, 0], [[-hx, hy, -hz], [hx, hy, -hz], [hx, hy, hz], [-hx, hy, hz]]],
      [[0, -1, 0], [[-hx, -hy, hz], [hx, -hy, hz], [hx, -hy, -hz], [-hx, -hy, -hz]]],
      [[0, 0, 1], [[-hx, -hy, hz], [-hx, hy, hz], [hx, hy, hz], [hx, -hy, hz]]],
      [[0, 0, -1], [[hx, -hy, -hz], [hx, hy, -hz], [-hx, hy, -hz], [-hx, -hy, -hz]]],
    ];
    for (const [nrm, vs] of F) {
      const idx = vs.map((v) =>
        push(m, cx + v[0], cy + v[1], cz + v[2], nrm[0], nrm[1], nrm[2]));
      quad(m, idx[0], idx[1], idx[2], idx[3]);
    }
    if (part) {
      const r = m.parts[part] || (m.parts[part] = { from: iFrom, to: iFrom,
                                                   pivot: [0, 0, 0] });
      r.to = m.i.length;
    }
    return m;
  }

  /* A cylinder along +Y, capped.  Used for lens barrels and can bodies.
   *
   * `part` is accepted for symmetry with `box` and is honoured the same
   * way, because a part range that covered only part of a cylinder would
   * slice triangles in half - and every caller here passes a name or null
   * deliberately rather than by accident. */
  function cyl(m, cx, cy, cz, r, h, seg, part, cap) {
    const iFrom = m.i.length;
    for (let i = 0; i < seg; i++) {
      const a0 = (i / seg) * TAU, a1 = ((i + 1) / seg) * TAU;
      const c0 = Math.cos(a0), s0 = Math.sin(a0);
      const c1 = Math.cos(a1), s1 = Math.sin(a1);
      const p0 = push(m, cx + c0 * r, cy, cz + s0 * r, c0, 0, s0);
      const p1 = push(m, cx + c1 * r, cy, cz + s1 * r, c1, 0, s1);
      const p2 = push(m, cx + c1 * r, cy + h, cz + s1 * r, c1, 0, s1);
      const p3 = push(m, cx + c0 * r, cy + h, cz + s0 * r, c0, 0, s0);
      quad(m, p0, p1, p2, p3);
      if (cap) {
        const t = push(m, cx, cy + h, cz, 0, 1, 0);
        quad(m, t, p2, p3, t);
        const b = push(m, cx, cy, cz, 0, -1, 0);
        quad(m, b, p1, p0, b);
      }
    }
    if (part) {
      const r = m.parts[part] || (m.parts[part] = { from: iFrom, to: iFrom,
                                                   pivot: [0, 0, 0] });
      r.to = m.i.length;
    }
    return m;
  }

  /* A U-shaped yoke: two arms and a crossbar, with a hole between them.
   * Built as five boxes rather than one solid, so a moving head reads as a
   * moving head rather than as a lump. */
  function yoke(m, w, h, t, gap, part) {
    box(m, 0, 0, 0, w, t, 0.02, part);                 // crossbar
    box(m, -w + t * 0.5, h * 0.5, 0, t * 0.5, h * 0.5, 0.02, part);
    box(m, w - t * 0.5, h * 0.5, 0, t * 0.5, h * 0.5, 0.02, part);
    const r = m.parts[part];
    if (r) r.pivot = [0, 0, 0];
    return m;
  }

  /* ---------------------------------------------------------------------
   * The bodies.  Each returns a shape built once and cached; the caller's
   * per-frame cost is a uniform change and a draw.
   *
   * HANGING vs FLOOR.  Every body is built hanging DOWN from its origin,
   * because that is how a truss fixture bolts on and how the whole
   * visualiser has always placed a hanging head.  A floor fixture has to
   * stand UP instead, and doing that by negating Y would mirror the model -
   * so it is done by a ROTATION in the instance transform, never in the
   * geometry.  The geometry stays honest; the placement is the caller's.
   * ------------------------------------------------------------------- */

  /* A moving head's parts, assembled into ONE interleaved shape.
   *
   * The yoke and head are built as separate little meshes and then
   * concatenated, with each part recording where its vertices ended up.
   * That indirection is what lets the renderer rotate them independently
   * while still handing WebGL one buffer: the parts are index ranges inside
   * a single mesh, not separate objects.
   *
   * The first version left the yoke in its own mesh and pointed the part
   * record at it, so the two were never in the same buffer and the disjoint
   * test below failed.  Concatenating is one extra pass at build time and
   * makes the renderer's job uniform: one buffer, several index ranges. */
  function assemble(pieces) {
    const m = mesh();
    let vBase = 0;      // vertex offset for the next piece
    let iBase = 0;      // INDEX offset for the next piece
    for (const [name, sub, pivot] of pieces) {
      for (let i = 0; i < sub.p.length; i++) {
        m.p.push(sub.p[i]);
        m.n.push(sub.n[i]);
      }
      for (let i = 0; i < sub.i.length; i++) m.i.push(sub.i[i] + vBase);
      // A part's range is in INDICES, because that is what the renderer
      // hands to drawElements as a byte/element offset.  The first version
      // used `sub.i.length` as the end while starting from a VERTEX offset,
      // so the two ends of the range were in different units - which is
      // only visible when the two counts differ, i.e. on every part of
      // every moving head, and it silently sliced the wrong triangles.
      m.parts[name] = {
        from: iBase,
        to: iBase + sub.i.length,
        pivot: pivot || [0, 0, 0],
      };
      vBase += sub.p.length / 3;
      iBase += sub.i.length;
    }
    return m;
  }

  const SHAPES = {
    /* A moving head: base, yoke that pans, head that tilts, lens.
     * The two rotation parts are separate so the DMX can drive them
     * independently - which is the entire point of a moving head. */
    mover: function () {
      const base = mesh();
      box(base, 0, 0.03, 0, 0.075, 0.03, 0.05);            // base plate
      cyl(base, 0, 0.06, 0, 0.055, 0.04, 10, null, true);  // the pivot boss
      // The yoke, built about its own pivot so the arms hang below it and
      // rotating the part rotates the head, not the base.
      const y = mesh();
      box(y, 0, -0.008, 0, 0.072, 0.016, 0.022);           // crossbar
      box(y, -0.058, -0.12, 0, 0.014, 0.112, 0.022);        // left arm
      box(y, 0.058, -0.12, 0, 0.014, 0.112, 0.022);         // right arm
      // The head, tipping about the crossbar - which is BELOW the yoke's
      // pivot reference, so the two rotations compose the way a real
      // moving head's do.
      const h = mesh();
      box(h, 0, -0.06, 0, 0.045, 0.062, 0.05);            // head body
      cyl(h, 0, -0.108, 0.028, 0.035, 0.05, 10, null, true); // lens barrel
      const m = assemble([
        ["base", base, [0, 0, 0]],
        // The yoke turns about the VERTICAL axis through the base's boss,
        // which is its own origin.
        ["yoke", y, [0, 0, 0]],
        // The head tips about the yoke's CROSSBAR, 8 mm below the yoke's
        // own pivot reference - and that ordering is the whole mechanism.
        // A head whose pivot sits above its yoke tilts the wrong way round,
        // and it looks like a broken fixture rather than a reversed one.
        ["head", h, [0, -0.008, 0]],
      ]);
      m.size = { w: 0.20, h: 0.34, d: 0.12 };
      return m;
    },

    /* A wash: one housing that pans about its mount.  A yoke with nothing
     * in it, which is what a wash actually is. */
    wash: function () {
      const base = mesh();
      box(base, 0, 0, 0, 0.06, 0.05, 0.05);
      const y = mesh();
      box(y, 0, -0.01, 0, 0.075, 0.055, 0.07);
      cyl(y, 0, -0.072, 0, 0.06, 0.03, 12, null, true);
      const m = assemble([
        ["base", base, [0, 0, 0]],
        ["yoke", y, [0, 0, 0]],
      ]);
      m.size = { w: 0.18, h: 0.20, d: 0.16 };
      return m;
    },

    /* A PAR can: a cylinder, tilted lens, on a bracket.  The most common
     * fixture in any rig and the one a fallback gets seen most, so it is
     * worth more than a box. */
    par: function () {
      const base = mesh();
      box(base, 0, 0.02, 0, 0.05, 0.02, 0.04);
      const y = mesh();
      cyl(y, 0, -0.24, 0, 0.065, 0.26, 14, null, true);
      // A lens plate on the front, slightly proud so it catches light.
      box(y, 0, -0.24, 0.062, 0.05, 0.05, 0.006);
      const m = assemble([
        ["base", base, [0, 0, 0]],
        ["yoke", y, [0, 0, 0]],
      ]);
      m.size = { w: 0.14, h: 0.30, d: 0.14 };
      return m;
    },

    /* A batten or bar: long, thin, and it does not aim. */
    tube: function () {
      const m = mesh();
      box(m, 0, 0, 0, 0.55, 0.045, 0.045, "base");
      box(m, -0.5, 0, 0, 0.05, 0.05, 0.05, "base");
      box(m, 0.5, 0, 0, 0.05, 0.05, 0.05, "base");
      m.size = { w: 1.1, h: 0.12, d: 0.12 };
      return m;
    },

    /* A blinder: a shallow rectangular face, because that is the shape
     * and it is what makes a rig read as a rig. */
    panel: function () {
      const m = mesh();
      box(m, 0, 0, 0, 0.22, 0.10, 0.05, "base");
      box(m, 0, 0, 0.055, 0.20, 0.08, 0.006, "base");
      m.size = { w: 0.46, h: 0.22, d: 0.12 };
      return m;
    },

    /* The last resort: a small can.  Deliberately plain - this is the
     * "we do not know what this is" answer and it should look like it. */
    can: function () {
      const base = mesh();
      box(base, 0, 0, 0, 0.05, 0.07, 0.05);
      const y = mesh();
      cyl(y, 0, -0.07, 0, 0.05, 0.10, 10, null, true);
      const m = assemble([
        ["base", base, [0, 0, 0]],
        ["yoke", y, [0, 0, 0]],
      ]);
      m.size = { w: 0.12, h: 0.20, d: 0.12 };
      return m;
    },
  };

  /* The same decision `bodyFor` made, so a fixture keeps the identity it
   * always had and the 2D and 3D worlds agree about what a thing is. */
  /* ---------------------------------------------------------------------
   * WHICH BODY A FIXTURE GETS
   * ------------------------------------------------------------------- */

  /* Brand knowledge, moved here from viz.js when the 2D sprite bodies were
   * retired.
   *
   * This is worth keeping and worth moving, because it is the only part of
   * the old identity system that knew what a fixture ACTUALLY was.  Channel
   * roles say what a fixture can do; they do not say whether it is a Chauvet
   * Intimidator or a generic wash, and an operator recognises the product
   * long before they read the channel list.  Role-only classification threw
   * that away and would have drawn every wash in the rig identically.
   *
   * Keyed by a lower-cased manufacturer+model substring, most specific
   * first, because "intimidator" has to beat the bare "spot" rule. */
  const BRAND_RULES = [
    ["intimidator", "mover"], ["spot 260", "mover"], ["spot", "mover"],
    ["moving head", "mover"], ["profile", "mover"], ["beam", "tube"],
    ["bar", "tube"], ["batten", "tube"], ["strip", "tube"],
    ["wash", "panel"], ["blinder", "panel"], ["strobe", "panel"],
    ["par", "par"],
  ];

  /* Which body a fixture gets.
   *
   * Brand first, then channel roles, then the channel count - in that order,
   * and the order is the point.  A profile with 13 channels and a pan channel
   * is still a moving head: gobo, zoom, wheel and strobe all say the fixture
   * HAS A HEAD, so they are checked before the count gets a say.  An earlier
   * version checked the count first and turned every real profile into a
   * batten.
   *
   * `key` is the lower-cased manufacturer+model; it is optional, and without
   * it this is purely a classification of the channel roles. */
  function shapeFor(roles, key) {
    if (key) {
      for (const [needle, name] of BRAND_RULES) {
        if (key.indexOf(needle) >= 0) return name;
      }
    }
    if (!roles || !roles.length) return "can";
    const has = (r) => roles.indexOf(r) >= 0;
    if (has("pan") && has("tilt")) return "mover";
    if (has("gobo") && has("zoom")) return "mover";
    if (has("wheel") || has("strobe")) return "mover";
    if (has("pan") || has("tilt")) return "wash";
    // No aiming.  Now the body is a shape question, and the channel count
    // is the honest signal: a wide channel count is a batten, a narrow one
    // is a panel, and a single dimmer is whatever it is.
    if (roles.length >= 12) return "tube";
    if (roles.length >= 8) return "panel";
    if (has("shutter") || roles.length >= 3) return "par";
    return "can";
  }

  /* The bridge from a visualiser fixture object to a body, so the caller
   * does not have to remember to build the brand key. */
  function shapeForFixture(f) {
    if (!f) return "can";
    const key = ((f.manufacturer || "") + " " + (f.model || "")).toLowerCase();
    const roles = (f.map || []).map((r) => String(r).toLowerCase());
    return shapeFor(roles, key);
  }

  /* One shape per name, ever.  The cache is what makes a generated body
   * cheap: the geometry is built once and every instance of that shape
   * draws the same buffer with a different transform. */
  const _shapes = Object.create(null);
  function shape(name) {
    let s = _shapes[name];
    if (!s) {
      // An unknown name falls back to the plain can AND IS CACHED UNDER
      // THE NAME IT WAS ASKED FOR.  The first version returned the can
      // itself, so every caller asking for "nonesuch" got the same object
      // while `_shapes` recorded nothing - which meant the cache could not
      // be compared against the set of shapes to prove it was bounded.
      const make = SHAPES[name] || SHAPES.can;
      s = make();
      s.name = name;
      s.p = new Float32Array(s.p);
      s.n = new Float32Array(s.n);
      s.i = (s.p.length / 3) > 65535
        ? new Uint32Array(s.i) : new Uint16Array(s.i);
      s.wide = s.i instanceof Uint32Array;
      s.tris = s.i.length / 3;
      _shapes[name] = s;
    }
    return s;
  }

  function shapeNames() { return Object.keys(SHAPES); }
  function cachedNames() { return Object.keys(_shapes); }

  /* ---------------------------------------------------------------------
   * AIM - the maths a body articulates with, as a pure function
   * -------------------------------------------------------------------
   *
   * WHY THIS IS HERE AND NOT IN viz.js
   *
   * The two sign conventions in this project are genuinely easy to get
   * backwards, and a wrong one is invisible on a still: a head aimed the
   * wrong way still LOOKS aimed, and the beam still comes out of the lens.
   * The twin works in a +Z-up row-vector frame and converts with
   * `swapYZ(transpose(M))`; a generated body is authored directly in the
   * +Y-up frame.  `swapYZ` is a reflection, so the twin's whole map has
   * determinant -1 and cannot be handed to a body authored in the other
   * frame without mirroring the fixture.
   *
   * So the equivalent PROPER rotation is the same map conjugated by the
   * axis swap, `S·Mᵀ·S`.  Conjugating a rotation by a reflection keeps the
   * magnitude and reflects the axis, which for a row-vector profile frame
   * lands on:
   *
   *     pan  ->  -panDeg about +Y
   *     tilt ->  -tiltDeg about +X
   *
   * NEGATED by the reflection, and in DEGREES, because that is the unit the
   * engine reports `panDeg`/`tiltDeg` in and the unit the twin takes.  This
   * function is the single place that conversion happens, and it is here
   * rather than inline in a draw call because it is the only way to TEST
   * it - a node process has no WebGL, so anything buried in the render loop
   * can only be checked by reading it, and reading it is what produced the
   * original error.
   */

  function mIdent(o) {
    o[0] = 1; o[1] = 0; o[2] = 0; o[3] = 0;
    o[4] = 0; o[5] = 1; o[6] = 0; o[7] = 0;
    o[8] = 0; o[9] = 0; o[10] = 1; o[11] = 0;
    o[12] = 0; o[13] = 0; o[14] = 0; o[15] = 1;
    return o;
  }
  function m4() { return new Float32Array(16); }
  // Fills a scratch before writing `o`, so `o` may alias `a` or `b`.
  const _mul = new Float32Array(16);
  function mMul(o, a, b) {
    for (let c = 0; c < 4; c++) {
      for (let r = 0; r < 4; r++) {
        _mul[c * 4 + r] =
          a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] +
          a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
      }
    }
    o.set(_mul);
    return o;
  }
  function mTrans(o, x, y, z) {
    mIdent(o); o[12] = x; o[13] = y; o[14] = z; return o;
  }
  /* Right-handed about +X: y' = y·c - z·s, z' = y·s + z·c.  Standard
   * column-vector form, which is the frame the body is authored in. */
  function mRotX(o, a) {
    const c = Math.cos(a), s = Math.sin(a);
    mIdent(o); o[5] = c; o[6] = s; o[9] = -s; o[10] = c; return o;
  }
  /* Right-handed about +Y: x' = x·c + z·s, z' = -x·s + z·c.  So a -90 pan
   * sends (0,0,1) to (-1,0,0), which is what the harness pins below.
   * NOT the other rotation helper in viz.js, which uses the old
   * projector's sign convention - using that one here is how a pan comes
   * out mirrored. */
  function mRotY(o, a) {
    const c = Math.cos(a), s = Math.sin(a);
    mIdent(o); o[0] = c; o[2] = -s; o[8] = s; o[10] = c; return o;
  }

  /* A part's spin about its own pivot: T(pivot) · R · T(-pivot).
   * Turning about the pivot rather than the body's origin is the whole
   * difference between a yoke that swings its head around and one that
   * orbits it - on a still both look like a head that moved, in motion the
   * second is a head that came off its fixture. */
  function pivotSpin(o, pivot, build) {
    const A = m4(), B = m4(), R = m4();
    mTrans(A, pivot[0], pivot[1], pivot[2]);
    mTrans(B, -pivot[0], -pivot[1], -pivot[2]);
    build(R);
    mMul(o, A, R);
    mMul(o, o, B);
    return o;
  }

  /* The two matrices a body draws with: the yoke's, and the head's.
   *
   * pan is OUTSIDE tilt because the head hangs off the yoke, so the head
   * tilts about its own transverse axis FIRST and the whole yoke then
   * carries that about the vertical.  Reversed, a head tilts about the
   * wrong axis and sweeps a circle on a real pan.
   *
   * Both pivots are optional; without one the part turns about the body's
   * origin, which is what a base does. */
  function aimMatrix(yokePivot, headPivot, panDeg, tiltDeg) {
    // Identity FIRST, not zero.  A `m4()` is a zero-filled array, and the
    // no-pivot / no-angle path below touches neither `mIdent` nor `mRotY`,
    // so without this the function returned an all-ZERO matrix - which
    // collapses a body to a point instead of leaving it where it is.  The
    // engine can legitimately send a NaN angle, and `NaN || 0` is 0, so
    // that path is reachable and it is the common one for a head that has
    // no pan or tilt channel at all.
    const yoke = mIdent(m4());
    if (yokePivot) {
      pivotSpin(yoke, yokePivot, (m) => mRotY(m, -(panDeg || 0) * DEG));
    } else if (panDeg) {
      mRotY(yoke, -(panDeg || 0) * DEG);
    }
    const head = m4();
    if (!tiltDeg) {
      head.set(yoke);                       // no tilt: the head rides the yoke
    } else if (headPivot) {
      const t = m4();
      pivotSpin(t, headPivot, (m) => mRotX(m, -(tiltDeg || 0) * DEG));
      mMul(head, yoke, t);
    } else {
      const t = m4();
      mRotX(t, -(tiltDeg || 0) * DEG);
      mMul(head, yoke, t);
    }
    return { yoke: yoke, head: head };
  }
  const DEG = Math.PI / 180;

  /* Where does a point go, and what does a direction become?  Exposed so
   * the harness can check the aim without a GL context - this is what
   * makes the sign convention testable rather than asserted. */
  function xformPoint(m, p) {
    return [
      m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
      m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
      m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14],
    ];
  }
  function xformDir(m, d) {
    return [
      m[0] * d[0] + m[4] * d[1] + m[8] * d[2],
      m[1] * d[0] + m[5] * d[1] + m[9] * d[2],
      m[2] * d[0] + m[6] * d[1] + m[10] * d[2],
    ];
  }
  /* Determinant of the upper 3x3, which is +1 for a rotation and -1 for
   * anything that mirrors.  A generated body fed a -1 matrix renders inside
   * out, which is exactly the mistake this guards. */
  function det3(m) {
    return m[0] * (m[5] * m[10] - m[6] * m[9])
         - m[4] * (m[1] * m[10] - m[2] * m[9])
         + m[8] * (m[1] * m[6] - m[2] * m[5]);
  }

  root.Fixture3D = {
    shape: shape, shapeFor: shapeFor, shapeForFixture: shapeForFixture,
    shapeNames: shapeNames, cachedNames: cachedNames,
    brandRules: BRAND_RULES,
    aimMatrix: aimMatrix, xformPoint: xformPoint, xformDir: xformDir,
    det3: det3,
    mesh: mesh, box: box, cyl: cyl, yoke: yoke,
  };
})(typeof window !== "undefined" ? window : globalThis);
