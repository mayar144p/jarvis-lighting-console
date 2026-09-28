"""The JS harnesses for web/fixture3d.js, run under node by the selftest.

WHY A SEPARATE MODULE

selftest is already enormous and the JS half needs a different runner, so
the harness strings live here and the suite only wires them to node.  This
is the same shape as _gdtf_harness.py.

WHAT THESE CAN AND CANNOT SEE

They can see MATRICES.  A node process has no WebGL, so nothing here can
see a pixel - but pan, tilt, pivot and composition are all pure matrix
algebra, and a pure matrix algebra test is exactly as strong as a
screenshot for those.  The convention is the part that is invisible when
wrong: a head aimed the wrong way still looks aimed, and the beam still
leaves the lens, so it has to be pinned numerically or not at all.

They must stay SYNTHETIC.  Nothing here reads the operator's .gdtf files.
"""

SHAPE_HARNESS = r'''
const fs = require('fs');
(0, eval)(fs.readFileSync(process.argv[2], 'utf8'));
const F = globalThis.Fixture3D;
if (!F) { console.log('FAIL no Fixture3D'); process.exit(2); }

let pass = 0, fail = 0;
function ok(c, label, detail) {
  if (c) { pass++; console.log('    ok   ' + label); }
  else { fail++; console.log('    FAIL ' + label + (detail ? '  ' + detail : '')); }
}
const near = (a, b, e) => a.every((v, i) => Math.abs(v - b[i]) < (e || 1e-6));

// Every shape must build, and be a real mesh.
for (const name of F.shapeNames()) {
  const s = F.shape(name);
  ok(s.p.length > 0 && s.i.length > 0 && s.p.length / 3 === s.n.length / 3,
     'shape "' + name + '" builds a closed mesh', s.p.length / 3 + ' verts');
  ok(s.i.length % 3 === 0 && s.tris > 0,
     'shape "' + name + '" indexes whole triangles', s.tris + ' tris');
  ok(s.p.every((v) => isFinite(v)) && s.n.every((v) => isFinite(v)),
     'shape "' + name + '" has no NaN anywhere');
  // Every index must name a real vertex.  A body drawn from a bad index
  // reads off the end of the buffer and hangs the tab.
  const nv = s.p.length / 3;
  let max = 0;
  for (let i = 0; i < s.i.length; i++) if (s.i[i] > max) max = s.i[i];
  ok(max < nv, 'shape "' + name + '" indexes in range', max + ' of ' + nv);
  // Normals must be UNIT.  The shader normalises, so a zero or a 3-length
  // normal is not fatal - but a builder that got it wrong would light the
  // whole rig wrong, so it is checked rather than assumed.
  let allUnit = true;
  for (let i = 0; i < s.n.length; i += 3) {
    const l = Math.hypot(s.n[i], s.n[i + 1], s.n[i + 2]);
    if (Math.abs(l - 1) > 1e-3) { allUnit = false; break; }
  }
  ok(allUnit, 'shape "' + name + '" normals are unit length');
  // Parts must be a contiguous index range, because the renderer uploads
  // and draws them as separate ranges.
  for (const k of Object.keys(s.parts)) {
    const p = s.parts[k];
    ok(p.to > p.from, 'shape "' + name + '" part "' + k + '" spans triangles',
       p.from + '..' + p.to);
  }
}

// A mover's yoke and head must be SEPARATE, and both must be able to
// rotate: that is the entire difference between a moving head and a lump.
const mv = F.shape('mover');
ok(!!mv.parts.yoke && !!mv.parts.head, 'a mover has a yoke AND a head');
ok(mv.parts.yoke.to <= mv.parts.head.from,
   'and they are disjoint index ranges, so each can be drawn on its own');
ok(mv.parts.head.to > mv.parts.head.from,
   'and the head has geometry of its own inside the shared buffer');
ok(mv.parts.yoke.pivot && mv.parts.head.pivot, 'both carry a pivot to rotate about');
// The head's pivot must be BELOW the yoke's - a head that pivots above its
// own yoke tilts the wrong way round, which looks like a broken fixture.
ok(mv.parts.head.pivot[1] < mv.parts.yoke.pivot[1],
   'the head pivots BELOW the yoke, so tilting looks like tilting',
   JSON.stringify(mv.parts.head.pivot) + ' vs ' + JSON.stringify(mv.parts.yoke.pivot));

// Sizes must be real, in metres.  A body that is the wrong size sits in
// the scene pretending, which is worse than no body at all.
ok(mv.size.h > 0.2 && mv.size.h < 0.7,
   'a mover is 0.2-0.7 m tall, which is the real range', String(mv.size.h));
const par = F.shape('par');
ok(par.size.h > 0.15 && par.size.h < 0.5, 'a PAR can is 0.15-0.5 m', String(par.size.h));
const tube = F.shape('tube');
ok(tube.size.w > tube.size.h * 4, 'a batten is much wider than it is tall',
   tube.size.w + ' x ' + tube.size.h);

// Caching: the whole point is that a body is built once.
const a1 = F.shape('mover'), a2 = F.shape('mover');
ok(a1 === a2, 'the same shape name returns the SAME object, so a rig of 40 ' +
   'identical fixtures builds one body');
ok(F.cachedNames().length <= F.shapeNames().length,
   'and the cache cannot grow past the set of shapes',
   F.cachedNames().join(','));

// shapeFor must reproduce the decisions the 2D sprite bodies used to make,
// because a fixture that changes identity when the 3D bodies arrive is a
// fixture that moves.  Order matters and is the point: a profile with 13
// channels AND a pan channel is still a moving head, and checking the
// channel count first turned it into a batten.
const cases = [
  [['pan', 'tilt', 'dimmer'], 'mover'],
  [['pan', 'dimmer', 'red', 'green', 'blue'], 'wash'],
  [['gobo', 'zoom', 'dimmer', 'pan'], 'mover'],
  [['wheel', 'strobe', 'dimmer', 'pan', 'tilt'], 'mover'],
  [['shutter', 'dimmer', 'red', 'green', 'blue'], 'par'],
  // A batten: many channels, and NONE of them mean it aims.  gobo, zoom,
  // strobe and wheel all say "this fixture has a head", so a profile
  // carrying them is a mover however many channels it has - which is the
  // whole reason the aiming tests are checked before the count.
  [['dimmer', 'red', 'green', 'blue', 'white', 'amber', 'uv', 'lens',
    'aux1', 'aux2', 'aux3', 'aux4', 'aux5'], 'tube'],
  [['dimmer', 'red', 'green', 'blue', 'white', 'amber', 'uv', 'strobe',
    'shutter', 'gobo', 'zoom', 'prism', 'focus'], 'mover'],
  // The regression this order exists to prevent: a real profile with 13
  // channels and a pan channel is a MOVER, and checking the channel count
  // first turned it into a batten.
  [['pan', 'tilt', 'dimmer', 'red', 'green', 'blue', 'gobo', 'zoom',
    'prism', 'strobe', 'shutter', 'wheel', 'focus'], 'mover'],
  [['dimmer'], 'can'],
  [[], 'can'],
];
for (const [map, want] of cases) {
  const got = F.shapeFor(map);
  ok(got === want, map.length + ' roles incl. ' + (map[0] || 'none') +
     ' -> ' + want, 'got ' + got);
}

// BRAND.  This is the part that knows what a fixture actually IS, and it is
// the only thing the 2D sprite bodies carried that a role list does not.
ok(F.shapeFor(['dimmer', 'red'], 'chauvet intimidator spot 140') === 'mover',
   'a named moving head is a mover');
ok(F.shapeFor(['dimmer', 'red'], 'chauvet led par') === 'par',
   'a named PAR is a par');
ok(F.shapeFor(['dimmer', 'red'], 'acme led batten 4') === 'tube',
   'a named batten is a tube');
ok(F.shapeFor(['dimmer', 'red'], 'acme led strobe') === 'panel',
   'a named strobe is a panel');
// Brand is checked before the count, and this is the case that shows it: two
// channels on their own are a "can", and the name turns it into a mover.
ok(F.shapeFor(['dimmer'], 'chauvet intimidator 140') === 'mover',
   'BRAND BEATS ROLES, because the product is more specific than the ' +
   'channel count');
ok(F.shapeFor(['dimmer'], 'acme led fixture 7') === 'can',
   '...and a name the table does not know still falls through to the ' +
   'roles, so the brand half is a refinement and not a replacement');

// ORDER IN THE BRAND TABLE.  "intimidator" has to beat a bare "spot" if both
// appear in one name, or a real product gets classified by the generic word
// that happens to be inside it.
const rules = F.brandRules;
ok(rules.findIndex((r) => r[0] === 'intimidator')
     < rules.findIndex((r) => r[0] === 'spot'),
   'the specific model name is listed BEFORE the generic word, so ' +
   '"intimidator spot" is not decided by the word "spot"');
ok(F.shapeFor([], 'chauvet intimidator') === 'mover',
   'and the order actually changes the answer, which is what makes it ' +
   'worth testing rather than assuming');
// Every rule must name a shape that exists, or it routes to the can and the
// table is quietly half-wrong.
let allReal = true;
for (const [needle, name] of rules) {
  if (F.shapeNames().indexOf(name) < 0) {
    allReal = false;
    console.log('      rule "' + needle + '" names shape "' + name + '"');
  }
}
ok(allReal, 'every brand rule names a shape that actually exists');
ok(rules.every((r) => typeof r[0] === 'string' && r[0] === r[0].toLowerCase()),
   'and every rule is keyed on a lower-cased substring, because the ' +
   'caller lower-cases the key and a mixed-case rule could never match');

// shapeForFixture: the bridge.  It must build the key from the two fields
// the engine sends and get the same answer.
ok(F.shapeForFixture({ manufacturer: 'Chauvet', model: 'Intimidator Spot 140',
                       map: ['pan', 'tilt', 'dimmer'] }) === 'mover',
   'shapeForFixture reads the manufacturer and model the engine sends');
ok(F.shapeForFixture({ model: 'LED PAR 64', map: ['dimmer'] }) === 'par',
   'and works from the model alone when there is no manufacturer');
ok(F.shapeForFixture({}) === 'can', 'an empty fixture is a can');
ok(F.shapeForFixture(null) === 'can', 'a null fixture is a can');
ok(F.shapeForFixture({ map: null, model: null }) === 'can',
   'a fixture with no map and no model is a can, not a crash');

const unknown = F.shape('nonesuch');
ok(unknown.tris === F.shape('can').tris,
   'an unknown shape name falls back to the plain can');
ok(F.cachedNames().indexOf('nonesuch') >= 0,
   'and is cached under the name it was asked for, so the cache stays '
   + 'inspectable and provably bounded');

console.log('    -- ' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
'''

AIM_HARNESS = r'''
const fs = require('fs');
(0, eval)(fs.readFileSync(process.argv[2], 'utf8'));
const F = globalThis.Fixture3D;
if (!F || !F.aimMatrix) { console.log('FAIL no aimMatrix'); process.exit(2); }

let pass = 0, fail = 0;
function ok(c, label, detail) {
  if (c) { pass++; console.log('    ok   ' + label); }
  else { fail++; console.log('    FAIL ' + label + (detail ? '  ' + detail : '')); }
}
const near = (a, b, e) => a.every((v, i) => Math.abs(v - b[i]) < (e || 1e-5));
const YP = [0, 0, 0];          // a mover's yoke pivots on its own origin
const HP = [0, -0.008, 0];     // the head tips about the crossbar, 8mm below
const D = (deg) => deg * Math.PI / 180;

// ---------- 1. zero aim is the identity, on BOTH parts ----------
{
  const a = F.aimMatrix(YP, HP, 0, 0);
  const I = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1];
  ok(near(Array.from(a.yoke), I), 'pan 0 tilt 0 leaves the yoke alone');
  ok(near(Array.from(a.head), I), 'pan 0 tilt 0 leaves the head alone');
}
{
  // A shape with no tilt part still gets a sane head matrix
  const a = F.aimMatrix(YP, null, 0, 0);
  ok(F.det3(a.head) > 0.99, 'a head with no pivot still gets a rotation',
     'det ' + F.det3(a.head));
}

// ---------- 2. NOTHING EVER MIRRORS ----------
// This is the property the whole conjugation argument exists for.  A body
// fed a determinant of -1 renders inside out, and a back-face-culled
// renderer then throws every visible face away - which is the culling bug
// this project already hit once.
for (const [p, t] of [[0,0],[90,0],[0,90],[90,45],[-135,270],[270,-90],[45,-45]]) {
  const a = F.aimMatrix(YP, HP, p, t);
  const dy = F.det3(a.yoke), dh = F.det3(a.head);
  ok(dy > 0.999 && dy < 1.001 && dh > 0.999 && dh < 1.001,
     'pan ' + p + ' tilt ' + t + ' is a pure rotation on both parts '
     + '(det 1, never -1)', 'yoke ' + dy.toFixed(4) + ' head ' + dh.toFixed(4));
}

// ---------- 3. the BASIS STAYS ORTHONORMAL ----------
// A shear or a scale would also keep a determinant near 1, so det alone is
// not enough.  Every column must stay unit length and mutually orthogonal.
for (const [p, t] of [[37,0],[0,73],[91,44],[-128,196]]) {
  const a = F.aimMatrix(YP, HP, p, t);
  const col = (m, i) => [m[i*4], m[i*4+1], m[i*4+2]];
  const dot = (u, v) => u[0]*v[0] + u[1]*v[1] + u[2]*v[2];
  const c0 = col(a.head, 0), c1 = col(a.head, 1), c2 = col(a.head, 2);
  const len = Math.hypot(c0[0], c0[1], c0[2]);
  ok(Math.abs(len - 1) < 1e-4 && Math.abs(dot(c0,c1)) < 1e-4
     && Math.abs(dot(c0,c2)) < 1e-4 && Math.abs(dot(c1,c2)) < 1e-4,
     'pan ' + p + ' tilt ' + t + ' leaves the basis orthonormal, so it is a '
     + 'rotation and not a shear', 'len ' + len.toFixed(6));
}

// ---------- 4. pan turns about the VERTICAL, and nothing else ----------
// A pan of 90 degrees must move the world +Z direction onto world X and
// leave world +Y exactly where it was.  If the rotation were about any
// other axis, or in the wrong handedness, one of these fails.
{
  const a = F.aimMatrix(YP, HP, 90, 0);
  const z = F.xformDir(a.yoke, [0, 0, 1]);
  const y = F.xformDir(a.yoke, [0, 1, 0]);
  ok(near(y, [0, 1, 0], 1e-5),
     'pan 90 leaves the VERTICAL alone - it pans, it does not nod',
     JSON.stringify(y));
  ok(near(z, [1, 0, 0], 1e-5) || near(z, [-1, 0, 0], 1e-5),
     'pan 90 swings the fore/aft axis onto the left/right axis',
     JSON.stringify(z));
}
{
  const a = F.aimMatrix(YP, HP, 90, 0);
  // mRotY here is the standard right-handed rotation about +Y:
  // (0,0,1) -> (sin(-90), 0, cos(-90)) = (-1, 0, 0).
  ok(near(F.xformDir(a.yoke, [0, 0, 1]), [-1, 0, 0], 1e-5),
     'and it is specifically -90 about +Y, not +90: (0,0,1) -> (-1,0,0).  '
     + 'The sign is negated by the twin\'s swapYZ, and +90 would send a pan '
     + 'the opposite way to every real head in the rig',
     JSON.stringify(F.xformDir(a.yoke, [0, 0, 1])));
}
{
  const a = F.aimMatrix(YP, HP, 0, 0);
  const a2 = F.aimMatrix(YP, HP, 360, 0);
  ok(near(Array.from(a2.yoke), Array.from(a.yoke), 1e-4),
     'a full 360 pan returns to where it started');
  const a3 = F.aimMatrix(YP, HP, 0, 360);
  ok(near(Array.from(a3.head), Array.from(a.head), 1e-4),
     'and so does a full 360 tilt, so the travel cannot creep');
}

// ---------- 5. tilt turns about the TRANSVERSE axis, and nothing else ----------
{
  const a = F.aimMatrix(YP, HP, 0, 90);
  const x = F.xformDir(a.head, [1, 0, 0]);
  ok(near(x, [1, 0, 0], 1e-5),
     'tilt 90 leaves the left/right axis alone - it nods, it does not pan',
     JSON.stringify(x));
  const z = F.xformDir(a.head, [0, 0, 1]);
  ok(near(z, [0, -1, 0], 1e-5) || near(z, [0, 1, 0], 1e-5),
     'tilt 90 swings the fore/aft axis onto the vertical',
     JSON.stringify(z));
}

// ---------- 6. a pivot does not move ----------
// The single most useful property, because it is what distinguishes a part
// that rotates in place from one that orbits.  The pivot point of a
// rotation is a FIXED POINT, so it must be unchanged by that part's own
// matrix - and it must be unchanged even when both pan and tilt are on.
{
  const a = F.aimMatrix([0.1, 0.2, 0.3], HP, 71, 43);
  const py = F.xformPoint(a.yoke, [0.1, 0.2, 0.3]);
  ok(near(py, [0.1, 0.2, 0.3], 1e-5),
     'the yoke pivot is a FIXED POINT of the yoke matrix, so the yoke '
     + 'swings in place instead of orbiting', JSON.stringify(py));
  // The head's pivot is fixed by the TILT, not by the whole: the pan
  // carries it around the yoke.  So it must move with the yoke and be
  // fixed relative to the yoke.
  const ph = F.xformPoint(a.head, HP);
  const rel = F.xformPoint(a.yoke, HP);
  ok(near(ph, rel, 1e-5),
     'and the head pivot rides the pan exactly, so the head tilts inside '
     + 'the yoke rather than sliding out of it',
     JSON.stringify(ph) + ' vs ' + JSON.stringify(rel));
}

// ---------- 7. ORDER: pan is OUTSIDE tilt ----------
// Pan-then-tilt and tilt-then-pan give different answers, and the wrong one
// is what makes a head sweep a circle on a real pan.  Pinned by computing
// both orders by hand and demanding the pan-outside one.
{
  const a = F.aimMatrix(YP, HP, 60, 30);
  const c = (d) => [Math.cos(D(d)), Math.sin(D(d))];
  // take a head-local point well away from both pivots
  const p = [0.05, -0.2, 0.07];
  function rx(v, ang) {
    const [c0, s0] = c(ang);
    return [v[0], v[1]*c0 - v[2]*s0, v[1]*s0 + v[2]*c0];
  }
  function ry(v, ang) {
    const [c0, s0] = c(ang);
    return [v[0]*c0 + v[2]*s0, v[1], -v[0]*s0 + v[2]*c0];
  }
  // translate to the head pivot, tilt, translate back
  const rel = [p[0]-HP[0], p[1]-HP[1], p[2]-HP[2]];
  let q = rx(rel, -30);
  q = [q[0]+HP[0], q[1]+HP[1], q[2]+HP[2]];
  // then the pan about the yoke pivot
  q = ry(q, -60);
  const want = F.xformPoint(a.head, p);
  ok(near(want, q, 1e-5),
     'pan 60 / tilt 30 equals pan applied AFTER tilt, matching the '
     + 'hand-built chain', JSON.stringify(want) + ' vs ' + JSON.stringify(q));
  // and the other order must NOT match, or this test proves nothing
  let r = ry(rel, -60);
  r = [r[0]+HP[0], r[1]+HP[1], r[2]+HP[2]];
  r = rx(r, -30);
  ok(!near(want, r, 1e-3),
     'and it is genuinely the other order from tilt-then-pan, so the test '
     + 'above is not passing by coincidence');
}

// ---------- 8. DEGREES, not radians ----------
// The engine reports panDeg/tiltDeg, and the twin takes degrees.  A harness
// or a caller that quietly passed radians would turn a 90-degree pan into
// 1.6 degrees, which looks like "the pan does not work".
{
  const a = F.aimMatrix(YP, HP, 180, 0);
  const z = F.xformDir(a.yoke, [0, 0, 1]);
  ok(near(z, [0, 0, -1], 1e-5),
     'pan 180 puts the fore/aft axis on itself reversed, so the unit is '
     + 'degrees - radians would be 3.14 and land nowhere near',
     JSON.stringify(z));
}

// ---------- 9. a missing or nonsense pivot must not throw ----------
{
  let threw = null;
  try {
    F.aimMatrix(null, null, 30, 30);
    F.aimMatrix(undefined, undefined, undefined, undefined);
    F.aimMatrix([0,0,0], [0,0,0], NaN, NaN);
  } catch (e) { threw = e; }
  ok(!threw, 'absent pivots and NaN angles do not throw', String(threw));
  const a = F.aimMatrix(null, null, NaN, NaN);
  ok(F.det3(a.head) > 0.99, 'and NaN does not produce a non-rotation',
     'det ' + F.det3(a.head));
}

console.log('    -- ' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
'''
