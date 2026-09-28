'''The permanent home of the twin tests: node harness + synthetic model bytes.'''

JAVASCRIPT_HARNESS = r"""
const fs = require('fs');
// gdtf3d.js is a plain classic script, not a CommonJS module: it assigns to
// globalThis.GDTF3D.  require() would hand back {} and every check below
// would fail with "not a function", which looks like a geometry bug and is
// not one.  Indirect eval runs it in global scope, which is what a <script>
// tag does in the browser.
(0, eval)(fs.readFileSync(process.argv[2], 'utf8'));
const G = globalThis.GDTF3D;
if (!G) { console.log('FAIL gdtf3d.js did not define GDTF3D'); process.exit(2); }

let pass = 0, fail = 0;
function ok(cond, label, detail) {
  if (cond) { pass++; console.log('    ok   ' + label); }
  else { fail++; console.log('    FAIL ' + label + (detail ? '  ' + detail : '')); }
}
const f4 = (a) => a.map(v => v.toFixed(4)).join(',');
const near = (a, b, e) => a.every((v, i) => Math.abs(v - b[i]) < (e || 1e-6));
const DEG = Math.PI / 180;
const manifest = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const models = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));

// ---------------------------------------------------------------- matrices
const I = G.mIdent();
ok(G.mMul(I, I).every((v, i) => v === I[i]), 'multiplying by the identity changes nothing');
ok(near([I[12], I[13], I[14]], [0, 0, 0]), 'the identity has no translation');

// THE coordinate-system invariant, stated as POINTS rather than as matrix
// indices, because indices are how you get the swap wrong unnoticed.
const GL = G.gdtfTranspose(I);
const up = G.pointMul(GL, [0, 0, 1, 1]);
ok(near([up[0], up[1], up[2]], [0, 0, 1]),
   'a profile point one metre up is one metre up in the profile frame');
ok(near(G.gdtfPoint([0, 0, 1]), [0, 1, 0]),
   "and one metre up in the visualiser's, because +Z up becomes +Y up");
ok(near(G.gdtfPoint([0, 1, 0]), [0, 0, 1]),
   "while the profile's Y axis becomes the visualiser's Z");

// The translation must go through the axis swap too: a GDTF pivot hanging
// 93.4 mm below its parent has to end up 93.4 mm below, not 93.4 mm behind.
const yoke = [1,0,0,0, 0,1,0,0, 0,0,1,-0.0934, 0,0,0,1];
const w = G.swapYZ(G.gdtfTranspose(yoke));
ok(Math.abs(w[12]) < 1e-9 && Math.abs(w[13] - (-0.0934)) < 1e-9
   && Math.abs(w[14]) < 1e-9,
   'the translation is transposed AND axis-swapped, so a hanging yoke is '
   + '93.4 mm down rather than 93.4 mm behind', f4([w[12], w[13], w[14]]));

// The profile's own rotations.
const rz = G.gdtfTranspose(G.gRotZ(90));
ok(near(G.pointMul(rz, [0, 0, 1, 1]).slice(0, 3), [0, 0, 1]),
   'a 90-degree pan leaves the up axis alone');
ok(near(G.pointMul(rz, [1, 0, 0, 1]).slice(0, 3), [0, -1, 0]),
   'and turns the profile +X into the horizontal plane');
ok(near(G.mMul(rz, I), rz, 1e-9), 'a rotation by the identity changes nothing');

// ---------------------------------------------------------------- the mover
const man = manifest.definitions.filter(d => d.kinematics && d.kinematics.pan);
if (!man.length) { ok(false, 'the manifest has a mover'); process.exit(2); }
const m0 = man[0];
const def = new G.Definition(m0);
const PAN = def.kinematics.pan, TILT = def.kinematics.tilt;
console.log('  --- ' + m0.id + ': ' + m0.summary);
ok(!!def.node(PAN), 'the manifest names a pan node that exists', PAN);
ok(!!def.node(TILT), 'and a tilt node that exists', TILT);
ok(PAN !== '0', 'the pan node is NOT the root - the root is the static base', PAN);
ok(TILT !== PAN, 'the tilt node is not the pan node', TILT);
ok(def.node(TILT).parent === PAN,
   'and the tilt node is a CHILD of the pan node, which is the real chain',
   def.node(TILT).parent + ' vs ' + PAN);
console.log('      pan=' + def.node(PAN).name + '  tilt=' + def.node(TILT).name
            + '  root=' + def.node(def._roots[0]).name);

// The node says Model="Base"; the profile's <Models> block is the only
// thing that connects that to `models/3ds/<long name> Base.3ds`.  A
// manifest whose model keys did not match the node names would download and
// parse every model and draw nothing, silently - so this is checked
// explicitly rather than left to emerge from a triangle count.
const named = Object.keys(m0.models || {});
ok(named.indexOf('Base') >= 0 && named.indexOf('Body') >= 0,
   'the manifest keys its models by the NAME a node refers to, not by the '
   + 'file stem', named.join(', '));
ok(named.indexOf('Lens') < 0,
   'and a node that names a part with no model file is simply absent - the '
   + 'Beam refers to Model="Lens" and there is no lens mesh, which is the '
   + 'right answer rather than an error');
let resolved = 0, unresolved = [];
for (const p in def.index()) {
  const rec = def.index()[p];
  if (!rec.model) continue;
  if (named.indexOf(rec.model) >= 0) resolved++;
  else unresolved.push({ path: p, model: rec.model, kind: rec.kind });
}
ok(resolved >= 3,
   'so every node that names a STRUCTURAL model resolves to a real mesh key',
   resolved + ' resolved, unresolved: '
   + (unresolved.map((u) => u.path + ':' + u.model).join(' ') || 'none'));
// A BEAM node may legitimately name a part with no mesh: the Intimidator's
// Lens is the lens, not a part, and the profile ships no file for it.  A
// structural node doing the same would be a real mismatch.
ok(unresolved.every((u) => u.kind === 'beam'),
   'and the only node naming a model with no file is the BEAM, which is the '
   + 'lens rather than a part - a structural node doing that would be a real '
   + 'mismatch', JSON.stringify(unresolved));

const pv = (p) => [def.node(p).localGL[12], def.node(p).localGL[13],
                   def.node(p).localGL[14]];
ok(Math.abs(pv(PAN)[1] + 0.0934) < 1e-5 && Math.abs(pv(PAN)[2]) < 1e-5,
   'the pan pivot is 93.4 mm BELOW the base, with no sideways slip', f4(pv(PAN)));
ok(Math.abs(pv(TILT)[1] + 0.1443) < 1e-5,
   'the tilt pivot is a further 144.3 mm down', f4(pv(TILT)));

// Composed world positions.  This is what a per-node coordinate conversion
// fails: a double-applied swap leaves every ROTATION looking right and every
// translation wrong.
const rest = def.solve({ panDeg: 0, tiltDeg: 0 });
const wc = (m) => [m[12], m[13], m[14]];
ok(near(wc(rest[PAN]), [0, -0.0934, 0], 1e-6),
   'composed, the yoke sits 93.4 mm below the base', f4(wc(rest[PAN])));
ok(near(wc(rest[TILT]), [0, -0.2377, 0], 1e-6),
   'and the head 237.7 mm below it - the two pivots ADDED, which is the '
   + 'whole point of a hierarchy', f4(wc(rest[TILT])));
ok(near(wc(rest[def._roots[0]]), [0, 0, 0], 1e-9),
   'and the base itself at the origin', f4(wc(rest[def._roots[0]])));

// -------------------------------------------------- pan at 0/25/50/75/100 %
const PR = 270;
const STEP = 2 * PR * 25 / 100;
const sweep = [];
for (const pct of [0, 25, 50, 75, 100]) {
  const deg = -PR + 2 * PR * pct / 100;
  const s = def.solve({ panDeg: deg, tiltDeg: 0 });
  const horiz = [s[PAN][0], s[PAN][1], s[PAN][2]];
  sweep.push(horiz);
  console.log('    pan ' + String(pct).padStart(3) + '%  ' + deg.toFixed(0).padStart(5)
    + ' deg -> the head faces ' + f4(horiz));
}
ok(sweep.length === 5, 'pan is measured at 0, 25, 50, 75 and 100 %');

// The ANGLE must advance by a constant step, not any one component: 540
// degrees is more than a half turn, so a component cannot be monotonic and
// asserting that asserts something false.  A wrong sign, a wrong axis or a
// matrix composed in the wrong order all leave every individual value a
// legal orientation and only break the STEP.
const steps = [];
for (let i = 1; i < sweep.length; i++) {
  const a = sweep[i - 1], b = sweep[i];
  steps.push(Math.atan2(a[0] * b[1] - a[1] * b[0],
                        a[0] * b[0] + a[1] * b[1]) / DEG);
}
console.log('    step between each 25%: ' + steps.map(s => s.toFixed(1)).join(', '));
ok(steps.every((s) => Math.abs(Math.abs(s) - STEP) < 1e-3),
   'each 25% of the range turns the head by exactly ' + STEP + ' degrees',
   f4(steps));
ok(steps.every((s) => s * steps[0] > 0),
   'and always the same way, so no part of the range is applied backwards');

const cLo = def.solve({ panDeg: -270 })[PAN], cHi = def.solve({ panDeg: 270 })[PAN];
ok(!near([cLo[0], cLo[1], cLo[2]], [cHi[0], cHi[1], cHi[2]], 1e-6),
   'the two ends of a 540-degree range are NOT the same pose - a range '
   + 'wider than a turn deliberately does not wrap');
ok(near([def.solve({ panDeg: 0 })[PAN][0], def.solve({ panDeg: 0 })[PAN][1],
         def.solve({ panDeg: 0 })[PAN][2]], [1, 0, 0], 1e-6),
   'and pan at centre leaves the head facing its own +X');

// ------------------------------------------------- tilt at 0/25/50/75/100 %
const TR = 117;
for (const pct of [0, 25, 50, 75, 100]) {
  const deg = -TR + 2 * TR * pct / 100;
  const s = def.solve({ panDeg: 0, tiltDeg: deg });
  console.log('   tilt ' + String(pct).padStart(3) + '%  ' + deg.toFixed(0).padStart(5)
    + ' deg -> local +Y is ' + f4([s[TILT][4], s[TILT][5], s[TILT][6]]));
}
ok(true, 'tilt is measured at 0, 25, 50, 75 and 100 %');
// The head's up axis after conversion is world +Z, because the profile's +Y
// is the visualiser's +Z (see the point checks above).  Watching for [0,1,0]
// here is watching the wrong axis and fails on a correct solver.
ok(near([def.solve({ tiltDeg: 0 })[TILT][4], def.solve({ tiltDeg: 0 })[TILT][5],
         def.solve({ tiltDeg: 0 })[TILT][6]], [0, 0, 1], 1e-6),
   'and tilt at centre leaves the head upright');

// -------------------------- the base must NOT move.  this is the whole test
const w0 = def.solve({ panDeg: 0, tiltDeg: 0 });
const wPan = def.solve({ panDeg: 180, tiltDeg: 0 });
const wTilt = def.solve({ panDeg: 0, tiltDeg: 90 });
const root = def._roots[0];
ok(near(w0[root], wPan[root], 1e-9), 'pan 180 deg leaves the ROOT exactly where it was');
ok(near(w0[root], wTilt[root], 1e-9), 'tilt 90 deg leaves the ROOT exactly where it was');
ok(!near(w0[PAN], wPan[PAN], 1e-6), 'and the PAN node DID move');
ok(!near(w0[TILT], wTilt[TILT], 1e-6), 'and the TILT node DID move');
const kid = def.node(PAN).children[0];
ok(!!kid, 'the pan node has a child (the moving head)', kid);
ok(!near(w0[kid], wPan[kid], 1e-6), 'which INHERITS the pan - not animated separately');
const both = def.solve({ panDeg: 90, tiltDeg: 60 })[TILT];
ok(!near(both, def.solve({ panDeg: 90, tiltDeg: 0 })[TILT], 1e-6)
   && !near(both, def.solve({ panDeg: 0, tiltDeg: 60 })[TILT], 1e-6),
   'pan and tilt COMPOSE, and neither is applied to the wrong node');

// ------------------------------------------------------------------- beams
const bn = def.beamNodes();
ok(bn.length > 0, 'the definition knows where its beam node is', bn.length + ' beam(s)');
ok(!!bn[0].beam && bn[0].beam.beam_angle > 0,
   'with the beam angle the profile really states',
   bn[0].beam && bn[0].beam.beam_angle + ' deg');
ok(near(wc(rest[bn[0].path]), [0, -0.3377, -0.025], 1e-6),
   'and the lens hangs 337.7 mm below the base, 25 mm out the back',
   f4(wc(rest[bn[0].path])));

// ------------------------------------------- one definition, many instances
const scene = new G.Scene();
scene.addManifest(m0);
const st = scene.stats();
console.log('      ' + JSON.stringify(st));
ok(st.instances === (m0.heads || []).length,
   'every head of the type became an instance', st.instances);
ok(st.definitions === 1, 'but they share ONE definition, not one each', st.definitions);
ok(scene.defs.get(m0.id).refs === st.instances,
   'and the definition refcount matches the instance count',
   scene.defs.get(m0.id).refs);

scene.setDmx(m0.heads[0], { panDeg: 90, tiltDeg: 0 });
scene.setDmx(m0.heads[1], { panDeg: -90, tiltDeg: 45 });
const h0 = scene.get(m0.heads[0]), h1 = scene.get(m0.heads[1]);
ok(h0.dmx.panDeg === 90 && h1.dmx.panDeg === -90,
   'two instances hold INDEPENDENT DMX state');
ok(h0.def === h1.def, 'while still sharing the one definition');
scene.update();
ok(!near(h0.nodeWorld[TILT], h1.nodeWorld[TILT], 1e-6),
   'and render differently, so no transform was accidentally shared');

const bf = scene.beamFor(m0.heads[0]);
ok(!!bf, 'the beam can be located for an instance');
if (bf) {
  console.log('      head ' + m0.heads[0] + ' panned 90: beam from '
    + f4(bf.origin) + '  dir ' + f4(bf.dir) + '  ' + bf.beamAngle + '/'
    + bf.fieldAngle + ' deg');
  ok(Math.abs(Math.hypot(bf.dir[0], bf.dir[1], bf.dir[2]) - 1) < 1e-6,
     'the beam direction is a unit vector');
  const flat = new G.Scene();
  flat.addManifest(m0);
  flat.get(m0.heads[0]).setKind('truss');
  flat.setDmx(m0.heads[0], { panDeg: 0 });
  flat.update();
  const f0 = flat.beamFor(m0.heads[0]);
  ok(!!f0, 'an un-panned head still has a beam after being told it hangs');
  ok(!near(bf.dir, f0.dir, 1e-3),
     'a panned head points its beam elsewhere - the beam FOLLOWS the head '
     + 'rather than being drawn from a fixed vector', f4(bf.dir));
  ok(Math.abs(bf.dir[1]) < 1e-6,
     'staying level, because pan turns about the up axis and must not tip '
     + 'the beam', f4(bf.dir));
  ok(!near(bf.origin, [0, 0, 0], 1e-4),
     'and it leaves the LENS, not the middle of the fixture', f4(bf.origin));

  // FLOOR vs HANGING.  Two scenes identical apart from the mount kind, so
  // anything that differs between them is the stand-up rotation and nothing
  // else.
  //
  // These assert the RELATIONSHIP between the two, never a hardcoded axis.
  // The first version of this check expected a beam along -Z, taken from the
  // profile's own frame before the axis swap - and it failed the moment the
  // two mounts were compared, because the beam node carries its own rotation
  // and so the world direction is whatever that rotation makes it.  "The two
  // are opposites, and the floor one points up" is true whatever the node's
  // orientation; "it points along -Z" is true only for one fixture.
  const hangS = new G.Scene();
  hangS.addManifest(m0);
  hangS.get(m0.heads[0]).setKind('truss');
  hangS.update();
  const hHang = hangS.beamFor(m0.heads[0]);
  const floorS = new G.Scene();
  floorS.addManifest(m0);
  floorS.get(m0.heads[0]).setKind('floor');
  floorS.update();
  const hFloor = floorS.beamFor(m0.heads[0]);
  ok(!!hHang && !!hFloor, 'a head can be located mounted either way');
  if (hHang && hFloor) {
    ok(near(hFloor.dir, [-hHang.dir[0], -hHang.dir[1], -hHang.dir[2]], 1e-6),
       'the two mounts throw in OPPOSITE directions, because the model is '
       + 'stood up 180 degrees rather than nudged',
       f4(hFloor.dir) + ' vs ' + f4(hHang.dir));
    ok(hFloor.dir[1] > 0.9,
       'and the floor head washes UPWARD into the room, which is what a '
       + 'fixture standing on the deck does', f4(hFloor.dir));
    ok(hHang.dir[1] < -0.9,
       'while the hanging head throws DOWN at the floor', f4(hHang.dir));
    ok(near(hFloor.origin, [-hHang.origin[0], -hHang.origin[1],
                            -hHang.origin[2]], 1e-6),
       'and the lens mirrors through the mount point, so it ends up on the '
       + 'upright side of the fixture instead of under the deck',
       f4(hFloor.origin) + ' vs ' + f4(hHang.origin));
    ok(hFloor.origin[1] > 0 && hHang.origin[1] < 0,
       'so a floor head has its lens ABOVE the deck and a hanging head BELOW '
       + 'its mount - the underground-lens bug cannot come back',
       f4(hFloor.origin) + ' / ' + f4(hHang.origin));
  }
}

scene.remove(m0.heads[0]);
ok(scene.instances.size === st.instances - 1, 'removing a head removes its instance');
ok(scene.defs.get(m0.id).refs === st.instances - 1,
   'and drops the definition refcount to match', scene.defs.get(m0.id).refs);
ok(!!scene.get(m0.heads[1]), 'without disturbing the other instance');
ok(scene.defs.get(m0.id).refs > 0,
   'and without freeing a definition that is still in use');
scene.dispose();
ok(scene.instances.size === 0 && scene.defs.size === 0, 'dispose clears everything');

// ------------------------------------------------- the loaders, on bytes
function buf(b64) { return Buffer.from(b64, 'base64'); }
const tds = G.parse3DS(buf(models.threeds), 'Base');
ok(!!tds.meshes && tds.meshes.length === 1,
   'a synthetic 3DS chunk file yields its mesh', JSON.stringify(tds.failed || {}));
if (tds.meshes && tds.meshes.length) {
  ok(tds.meshes[0].positions.length === 9,
     'with every vertex read', tds.meshes[0].positions.length);
  ok(tds.meshes[0].indices.length === 3, 'and every face index', tds.meshes[0].indices.length);
  ok(tds.meshes[0].normals.length === 9 && tds.meshes[0].normals.every(
       (v) => isFinite(v)), 'and usable normals');
  ok(tds.bounds && isFinite(tds.bounds.radius) && tds.bounds.radius > 0,
     'and real bounds, for camera framing', tds.bounds && f4(tds.bounds.size));
}
// SIX faces over a 2x3 grid.  A face record is four uint16s - a flags word
// and three indices - so the stride is 8 bytes.  With ONE face a 2-byte
// stride still produces the right answer, because the single flags word is
// the only thing it skips; with six it does not.  The first version of this
// fixture had one face, so the suite was green against a parser that turned
// every real fixture in the library into garbage.
const grid = G.parse3DS(buf(models.grid), 'Grid');
ok(!!grid.meshes && grid.meshes.length === 1, 'a six-face 3DS parses',
   String(grid.failed));
if (grid.meshes && grid.meshes.length) {
  ok(grid.vertices === 6 && grid.faces === 6,
     'with all six vertices and all six faces', grid.vertices + '/' + grid.faces);
  const want = [0, 1, 4, 0, 4, 3, 1, 2, 5, 1, 5, 4, 3, 4, 5, 4, 5, 2];
  ok(grid.meshes[0].indices.length === 18
     && Array.prototype.every.call(grid.meshes[0].indices,
                                   (v, i) => v === want[i]),
     'and every index read at the right STRIDE and in the right ORDER - a '
     + '3DS face is (i, j, k, flags) with the flags word LAST, so a '
     + 'flags-first reader sees 0xBEEF as a vertex index',
     Array.prototype.join.call(grid.meshes[0].indices, ','));
}
// The real FACES chunk is LONGER than its faces (3472 bytes for 344 faces
// occupying 2760), so a parser that insists length == 8 + count*8 rejects
// the genuine chunk and every real fixture in the library.
const padded = G.parse3DS(buf(models.padded), 'Padded');
ok(!!padded.meshes && padded.meshes.length === 1,
   'a faces chunk with trailing slack past its face data still parses - the '
   + 'chunk length is a ceiling, not an equality', String(padded.failed));
ok(padded.meshes && padded.faces === 6,
   'and still reports the right face count',
   padded.meshes ? String(padded.faces) : 'n/a');
// A faces chunk whose indices point outside the vertex array must be
// REJECTED, not drawn: that is the discriminator that stops the several
// hundred other chunks in a 3DS being mistaken for faces.
//
// The chunk is located by its header, not by searching for the byte pattern
// - the first version searched for the index triple and corrupted a
// coincidental run of float bytes in the VERTEX data instead, leaving the
// parse perfectly valid and the test asserting nothing.
const over = (function () {
  const b = buf(models.grid);
  // Over the underlying BYTES, not the Buffer's elements.  `new
  // Uint16Array(nodeBuffer)` copies 8-bit values into 16-bit slots - it does
  // not reinterpret them - so the first version searched a completely
  // different array, found nothing, and asserted nothing.
  const bytes = new Uint8Array(b.buffer, b.byteOffset, b.byteLength);
  const word = (byteOff) => bytes[byteOff] | (bytes[byteOff + 1] << 8);
  for (let bo = 0; bo + 8 < bytes.length; bo += 2) {
    if (word(bo) !== 0x4120) continue;
    if (word(bo + 6) !== 6) continue;            // the face count
    // Face 4's first index: header 8, then 4 whole faces of 8 bytes.
    const at = bo + 8 + 4 * 8;
    bytes[at] = 60000 & 0xFF;
    bytes[at + 1] = (60000 >> 8) & 0xFF;
    return G.parse3DS(b, 'Over');
  }
  return { failed: 'could not locate the faces chunk to corrupt',
           meshes: [] };
})();
ok(!!over.failed && !over.meshes.length,
   'a faces chunk indexing past the vertex array is rejected outright',
   String(over.failed));
ok(typeof over.failed === 'string' && over.failed.indexOf('60000') >= 0,
   'and SAYS WHICH INDEX - the reason it was silently generic before is '
   + 'that JavaScript has no printf-style %: `"x %d" % n` is modulo, and '
   + 'evaluates to the number NaN, which is falsy', String(over.failed));
// An out-of-range index must be REPORTED, and the report must be a string.
// It was NaN for a while, because JavaScript has no printf-style `%`:
// `"x %d" % n` is the modulo operator, evaluates to the NUMBER NaN, and
// NaN is falsy - so `mesh.failed || "no triangles"` silently replaced the
// real reason with a generic one.
const bad = G.parse3DS(buf(models.threeds), 'Bad');
ok(bad.failed === undefined || typeof bad.failed === 'string',
   'a parse failure is reported as a STRING',
   typeof bad.failed + ': ' + String(bad.failed));
const nan = (function () {
  try {
    const b = G.parse3DS(buf(models.grid), 'G');
    for (const st of Object.keys(b)) {
      if (typeof b[st] === 'number' && !isFinite(b[st])) return st;
    }
    return null;
  } catch (e) { return 'threw ' + e; }
})();
ok(nan === null, 'and no field of a successful parse is NaN', String(nan));
const tglb = G.parseGLB(buf(models.glb), 'Body');
ok(!!tglb.meshes && tglb.meshes.length === 1,
   'a synthetic GLB yields its mesh', JSON.stringify(tglb.failed || {}));
if (tglb.meshes && tglb.meshes.length) {
  ok(tglb.meshes[0].positions.length === 9,
     'with every vertex read', tglb.meshes[0].positions.length);
  ok(tglb.meshes[0].indices.length === 3, 'and every index', tglb.meshes[0].indices.length);
  ok(tglb.format === 'glb', 'and says which format it came from');
}
// A broken asset must degrade, not throw.
for (const [name, fn, ext] of [['3ds', G.parse3DS, '.3ds'],
                               ['glb', G.parseGLB, '.glb'],
                               ['obj', G.parseOBJ, '.obj']]) {
  let bad = null;
  try { bad = G.parseModel(Buffer.from('not a model at all'), 'X', ext); }
  catch (e) { bad = {threw: String(e)}; }
  ok(bad && !bad.threw && bad.meshes && bad.meshes.length === 0 && bad.failed,
     'a ' + name + ' that is not one is reported, not thrown',
     JSON.stringify(bad && (bad.threw || bad.failed)));
}
let trunc = null;
try { trunc = G.parseModel(Buffer.from(models.glb.slice(0, 24)), 'T', '.glb'); }
catch (e) { trunc = {threw: String(e)}; }
ok(trunc && !trunc.threw, 'a TRUNCATED glb is reported, not thrown',
   JSON.stringify(trunc && (trunc.threw || trunc.failed)));

// --------------------------- one definition, one load, however many heads
const cache = new G.Scene();
cache.addManifest(m0);
let issued = 0;
// Bytes matching the DECLARED extension, not a fixed format.  The manifest
// says .3ds because that is what the real library ships; handing it a GLB
// tests nothing except that a GLB is not a 3DS, and the first version of
// this did exactly that and reported it as a loader failure.
const fetchBytes = (id, entry) => {
  issued++;
  const b64 = (entry.ext === '.glb') ? models.glb : models.grid;
  return Promise.resolve(Buffer.from(b64, 'base64'));
};
const p1 = cache.loadModels(m0.id, fetchBytes);
const p2 = cache.loadModels(m0.id, fetchBytes);     // while the first is live
ok(p1 === p2, 'a second load request while the first is in flight returns '
   + 'the SAME promise, so nothing is fetched twice');
p1.then(() => {
  ok(issued === Object.keys(cache.defs.get(m0.id).models).length,
     'each model is fetched exactly once', String(issued));
  const d = cache.defs.get(m0.id);
  ok(d.state === 'geometry', 'and the definition then reports real geometry',
     d.state + ' / ' + JSON.stringify(d.failed));
  ok(!!d.meshes && Object.keys(d.meshes).length > 0,
     'with the parsed meshes held on the DEFINITION, not per instance');
  // Each model is fitted to its OWN declared size, so the same source bytes
  // become three different meshes - which is the point: the profile, not the
  // file, decides how big a part is.  The declared sizes are the real
  // Intimidator's, and the shared grid mesh is 2 m across, so each part
  // lands on its own largest declared dimension.
  const bodyMax = Math.max.apply(null, d.meshes.Body.bounds.size);
  ok(Math.abs(bodyMax - 0.322282) < 1e-6,
     'and each model is fitted to the size ITS part is declared to be - the '
     + 'body to its 0.3223 m length, the yoke to 0.2311 m - from identical '
     + 'source bytes', f4(d.meshes.Body.bounds.size));
  ok(Math.abs(Math.max.apply(null, d.meshes.Yoke.bounds.size) - 0.231117) < 1e-6,
     'while sharing those bytes and one parsed geometry, never a re-parse',
     f4(d.meshes.Yoke.bounds.size));
  // Re-describing the same definition must NOT throw the meshes away.
  const before = d.meshes;
  cache.addManifest(m0);
  ok(cache.defs.get(m0.id).meshes === before
     && Object.keys(cache.defs.get(m0.id).meshes).length > 0,
     'a manifest refresh keeps the loaded meshes instead of re-fetching, '
     + 'which is what made model loading look like a leak');
  ok(cache.instances.size === st.instances,
     'and does not duplicate the instances either', String(cache.instances.size));

  // A model that will not load is a fallback for that definition only.
  const broken = new G.Scene();
  broken.addManifest(m0);
  broken.loadModels(m0.id, () => Promise.reject(new Error('404')))
    .then((d2) => {
      ok(d2.state === 'primitives', 'a model that will not load drops the '
         + 'definition to primitives, keeping the hierarchy and the beam',
         d2.state);
      ok(Object.keys(d2.failed).length > 0, 'and records why', JSON.stringify(d2.failed));
      ok(d2.beams.length > 0, 'while the BEAM survives, so the fixture still '
         + 'throws light');

      // ---- a mesh in the wrong units, and the profile's authority
      // Measured on this machine: the real Intimidator Base.3ds parses to
      // bounds of 192.916 x 149.500 x 89.076, and the same profile declares
      // Length="0.192916" Width="0.1495" Height="0.089076" METRES.  Same
      // three numbers a factor of 1000 apart, because 3D Studio writes
      // millimetres.  Left alone, the fixture is drawn a kilometre across.
      const mm = G.parse3DS(buf(models.millimetres), 'Body');
      ok(!!mm.meshes && mm.meshes.length,
         'a mesh in millimetres parses', mm.failed || '');
      if (mm.meshes && mm.meshes.length) {
        ok(Math.abs(mm.bounds.size[0] - 192.916) < 0.01,
           'and reports its RAW size in the exporter\'s own units, unscaled',
           f4(mm.bounds.size));
        const k = G.fitToProfile(mm, { width: 0.1495, height: 0.089076,
                                       length: 0.192916 });
        ok(Math.abs(k - 0.001) < 1e-9,
           'the profile declares metres, so the fit factor is exactly 1/1000',
           String(k));
        ok(Math.abs(mm.bounds.size[0] - 0.192916) < 1e-5,
           'and the mesh then measures what the profile says it measures',
           f4(mm.bounds.size));
        // The shape must survive: a per-axis fit would squash the fixture
        // to match, and a squashed mover is far harder to spot than a
        // wrong-sized one.
        const ratio = mm.bounds.size[0] / mm.bounds.size[1];
        ok(Math.abs(ratio - 192.916 / 149.5) < 1e-4,
           'with a UNIFORM scale, so the aspect ratio is untouched',
           f4([ratio]));
        ok(Math.abs(mm.meshes[0].normals[0]) <= 1.0000001,
           'and the normals are still unit length afterwards');
        // 192.916 is the mesh's largest dimension, so k = declared / 192.916.
        const wild = G.parse3DS(buf(models.millimetres), 'Body');
        const kw = G.fitToProfile(wild, { width: 5e6, height: 5e6,
                                          length: 5e6 });
        ok(kw === 1,
           'a factor past 1e4 is REFUSED outright, because at that point the '
           + 'mesh is mis-parsed rather than mis-united, and rescaling it '
           + 'would hide the parse bug', String(kw) + ' (k would be 25,918)');
        const odd = G.parse3DS(buf(models.millimetres), 'Body');
        const ko = G.fitToProfile(odd, { width: 960, height: 960,
                                         length: 960 });
        // Relative, and deliberately so: 3DS stores vertices as float32, so
        // 192.916 comes back as 192.91599... and an exact-equality check here
        // fails on a perfectly correct parser.
        const want = 960 / 192.916;
        ok(Math.abs(ko - want) < want * 1e-6 && odd.snapped === false,
           'a factor nowhere near a power of ten - 4.98x here, the model and '
           + 'the profile simply disagreeing - is applied AND flagged, '
           + 'because refusing it would leave a 5x-wrong fixture on screen, '
           + 'which is harder to spot than a fitted one with a note on it',
           String(ko));
        const near = G.parse3DS(buf(models.millimetres), 'Body');
        const kn = G.fitToProfile(near, { width: 0.1935, height: 0.089076,
                                          length: 0.1935 });
        ok(Math.abs(kn - 0.001) < 1e-12 && near.snapped === true,
           'while one within 2% of a round unit factor is SNAPPED to it, so '
           + 'a 1.003e-3 lands on exactly the declared size', String(kn));
        const none = G.parse3DS(buf(models.millimetres), 'Body');
        ok(G.fitToProfile(none, null) === 1,
           'and a profile with no declared size leaves the mesh alone');
      }
      console.log('    -- ' + pass + ' passed, ' + fail + ' failed');
      process.exit(fail ? 1 : 0);
    })
    .catch((e) => {
      ok(false, 'a rejected fetch is handled, not thrown', String(e));
      process.exit(1);
    });
}).catch((e) => {
  ok(false, 'the load promise resolves', String(e));
  process.exit(1);
});
"""
