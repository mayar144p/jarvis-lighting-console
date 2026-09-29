// Procedural 3D fixtures: one builder per physical type, styled per brand.
//
// Every model follows one rig convention so the stage can drive any of
// them the same way:
//
//   root            the mounting point.  Floor-standing = identity; hung
//                   from a truss = flipped upside down by the stage.
//   └─ pan          rotates about local Y (a yoke's pan, or a bracket's
//                   aim for a static light)
//      └─ tilt      rotates about local X
//         └─ head   the lens faces local +Y; `emitters` sit on it
//
// So at pan 0 / tilt 0 every fixture shines straight "up" its own mount:
// up for a floor light, down for a hung one.  A GDTF model with real
// meshes replaces this for its heads (see gdtf.js); the interface is the
// same.
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";

const FINISH = {
  gloss: { roughness: 0.28, metalness: 0.35 },
  satin: { roughness: 0.45, metalness: 0.3 },
  matte: { roughness: 0.72, metalness: 0.15 },
  metal: { roughness: 0.3, metalness: 0.85 },
};

const matCache = new Map();

function housing(style) {
  const key = "h" + style.body + style.finish;
  if (!matCache.has(key)) {
    const f = FINISH[style.finish] || FINISH.satin;
    matCache.set(key, new THREE.MeshStandardMaterial({
      color: new THREE.Color(style.body), roughness: f.roughness,
      metalness: f.metalness, envMapIntensity: 0.55,
    }));
  }
  return matCache.get(key);
}

function trim(style) {
  const key = "t" + style.accent;
  if (!matCache.has(key)) {
    matCache.set(key, new THREE.MeshStandardMaterial({
      color: new THREE.Color(style.accent), roughness: 0.4, metalness: 0.2,
      envMapIntensity: 0.4,
    }));
  }
  return matCache.get(key);
}

function steel() {
  if (!matCache.has("steel")) {
    matCache.set("steel", new THREE.MeshStandardMaterial({
      color: 0x2a2d33, roughness: 0.5, metalness: 0.7, envMapIntensity: 0.6,
    }));
  }
  return matCache.get("steel");
}

// The lens is per head: its emissive colour is the light it is putting out.
function lensMaterial() {
  return new THREE.MeshStandardMaterial({
    color: 0x05070a, roughness: 0.08, metalness: 0.2,
    emissive: new THREE.Color(0, 0, 0), emissiveIntensity: 1,
    envMapIntensity: 1.2,
  });
}

// ---------------------------------------------------------------------------
// brand plate: the manufacturer's name, in the brand's accent colour
// ---------------------------------------------------------------------------
const logoCache = new Map();

function logoTexture(text, colour) {
  const key = text + colour;
  if (logoCache.has(key)) return logoCache.get(key);
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 128;
  const g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  g.fillStyle = colour;
  let size = 92;
  g.font = `800 ${size}px "Inter", "Segoe UI", system-ui, sans-serif`;
  while (g.measureText(text).width > 480 && size > 30) {
    size -= 4;
    g.font = `800 ${size}px "Inter", "Segoe UI", system-ui, sans-serif`;
  }
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(text, 256, 68);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  logoCache.set(key, tex);
  return tex;
}

function logo(body, width, height) {
  const text = (body.brand === "generic" ? "" : body.brand_name || "").toUpperCase();
  if (!text) return null;
  const mat = new THREE.MeshBasicMaterial({
    map: logoTexture(text, body.style.accent), transparent: true,
    depthWrite: false, toneMapped: false,
    polygonOffset: true, polygonOffsetFactor: -2,
  });
  mat.color.setScalar(0.85);
  return new THREE.Mesh(new THREE.PlaneGeometry(width, height), mat);
}

// ---------------------------------------------------------------------------
// geometry helpers
// ---------------------------------------------------------------------------
function rbox(w, h, d, r, mat) {
  const radius = Math.min(r, w / 2 - 1e-3, h / 2 - 1e-3, d / 2 - 1e-3);
  return new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 3, Math.max(radius, 0.001)), mat);
}

function cyl(rTop, rBottom, h, mat, seg = 32) {
  return new THREE.Mesh(new THREE.CylinderGeometry(rTop, rBottom, h, seg), mat);
}

/** A lathe along +Y from a list of [radius, y] points. */
function lathe(points, mat, seg = 40) {
  const pts = points.map(([r, y]) => new THREE.Vector2(r, y));
  return new THREE.Mesh(new THREE.LatheGeometry(pts, seg), mat);
}

/** A flat disc facing +Y. */
function disc(r, mat, seg = 32) {
  const m = new THREE.Mesh(new THREE.CircleGeometry(r, seg), mat);
  m.rotation.x = -Math.PI / 2;
  return m;
}

/** Hex-packed cell centres inside radius R: 1, 7, 19, 37 ... */
function hexCells(count, R) {
  const rings = count <= 1 ? 0 : count <= 7 ? 1 : count <= 19 ? 2 : 3;
  const pitch = rings ? R / (rings + 0.55) : 0;
  const out = [];
  for (let q = -rings; q <= rings; q++) {
    for (let r = -rings; r <= rings; r++) {
      const s = -q - r;
      if (Math.max(Math.abs(q), Math.abs(r), Math.abs(s)) > rings) continue;
      out.push([pitch * (q + r / 2), pitch * (r * Math.sqrt(3) / 2)]);
    }
  }
  return { cells: out, cellR: rings ? pitch * 0.42 : R * 0.85 };
}

/** A lens face at height y: one big lens or an LED cell array. */
function lensFace(head, y, R, cells, lens, opts = {}) {
  const bezel = disc(R * 1.02, opts.bezelMat || steel());
  bezel.position.y = y - 0.001;
  head.add(bezel);
  if (cells <= 1) {
    const l = disc(R * 0.9, lens);
    l.position.y = y;
    head.add(l);
    return;
  }
  const { cells: pts, cellR } = hexCells(cells, R);
  const geo = new THREE.CircleGeometry(cellR, 18);
  geo.rotateX(-Math.PI / 2);
  const mesh = new THREE.InstancedMesh(geo, lens, pts.length);
  const m = new THREE.Matrix4();
  pts.forEach(([x, z], i) => { m.makeTranslation(x, y + 0.002, z); mesh.setMatrixAt(i, m); });
  head.add(mesh);
}

// ---------------------------------------------------------------------------
// the rig skeleton every builder fills in
// ---------------------------------------------------------------------------
function skeleton() {
  const root = new THREE.Group();
  const pan = new THREE.Group();
  const tilt = new THREE.Group();
  root.add(pan);
  pan.add(tilt);
  return { root, pan, tilt, emitters: [], lenses: [], height: 0.3, radius: 0.25 };
}

function emitter(parent, y, radius, extra = {}) {
  const node = new THREE.Object3D();
  node.position.y = y;
  parent.add(node);
  return { node, radius, dir: new THREE.Vector3(0, 1, 0), ...extra };
}

/** A moving-head base with display, handles and the brand plate. */
function movingBase(sk, body, w, h, d) {
  const H = housing(body.style);
  const base = rbox(w, h, d, 0.03, H);
  base.position.y = h / 2;
  sk.root.add(base);
  const display = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.28, h * 0.32),
    new THREE.MeshBasicMaterial({ color: 0x1d4ed8, toneMapped: false }));
  display.material.color.multiplyScalar(0.55);
  display.position.set(-w * 0.2, h * 0.52, d / 2 + 0.001);
  sk.root.add(display);
  const plate = logo(body, w * 0.46, w * 0.11);
  if (plate) {
    plate.position.set(w * 0.16, h * 0.5, d / 2 + 0.002);
    sk.root.add(plate);
  }
  for (const s of [-1, 1]) {                         // carry handles
    const hnd = rbox(0.02, h * 0.5, d * 0.55, 0.008, steel());
    hnd.position.set(s * (w / 2 + 0.012), h * 0.55, 0);
    sk.root.add(hnd);
  }
  const bearing = cyl(w * 0.3, w * 0.32, 0.014, steel());
  bearing.position.y = h + 0.007;
  sk.root.add(bearing);
  const stripe = cyl(w * 0.322, w * 0.322, 0.004, trim(body.style));
  stripe.position.y = h + 0.002;
  sk.root.add(stripe);
  sk.pan.position.y = h + 0.012;
}

/** A U yoke in the pan group; returns the tilt axis height. */
function yoke(sk, body, innerHalf, armH, armW = 0.05, depth = 0.12) {
  const H = housing(body.style);
  const plate = rbox(innerHalf * 2 + armW * 2, 0.045, depth, 0.015, H);
  plate.position.y = 0.0225;
  sk.pan.add(plate);
  for (const s of [-1, 1]) {
    const arm = rbox(armW, armH, depth, 0.02, H);
    arm.position.set(s * (innerHalf + armW / 2), armH / 2 + 0.03, 0);
    sk.pan.add(arm);
    const hub = cyl(0.035, 0.035, 0.012, trim(body.style), 24);
    hub.rotation.z = Math.PI / 2;
    hub.position.set(s * (innerHalf + armW + 0.004), armH - 0.02, 0);
    sk.pan.add(hub);
  }
  const lw = Math.min(innerHalf * 1.4, 0.22);
  const plate2 = logo(body, lw, lw * 0.23);
  if (plate2) {
    plate2.rotation.y = Math.PI / 2;
    plate2.position.set(innerHalf + armW + 0.001, armH * 0.45, 0);
    sk.pan.add(plate2);
  }
  sk.tilt.position.y = armH - 0.02;
  return armH - 0.02;
}

// ---------------------------------------------------------------------------
// builders
// ---------------------------------------------------------------------------
function movingSpot(body, variant) {
  const sk = skeleton();
  const hybrid = variant === "hybrid";
  const beam = variant === "beam";
  const R = beam ? 0.1 : 0.125;
  const len = beam ? 0.3 : hybrid ? 0.5 : 0.44;
  movingBase(sk, body, beam ? 0.32 : 0.38, 0.14, beam ? 0.26 : 0.3);
  const axis = yoke(sk, body, R + 0.012, len * 0.62 + 0.06);
  const H = housing(body.style);
  const front = len * 0.55;
  const back = -len * 0.45;
  const lensR = beam ? 0.05 : R * 0.72;
  const shell = lathe([
    [0.001, back], [R * 0.55, back + 0.005], [R * 0.85, back + 0.04],
    [R, back + 0.1], [R, front - 0.06], [R * 1.06, front - 0.035],
    [R * 1.04, front], [lensR * 1.05, front],
  ], H);
  sk.tilt.add(shell);
  for (let i = 0; i < 4; i++) {                       // cooling ribs
    const rib = cyl(R * 1.012, R * 1.012, 0.008, steel());
    rib.position.y = back + 0.13 + i * 0.03;
    sk.tilt.add(rib);
  }
  const band = cyl(R * 1.065, R * 1.065, 0.012, trim(body.style));
  band.position.y = front - 0.03;
  sk.tilt.add(band);
  const lens = lensMaterial();
  sk.lenses.push(lens);
  const l = disc(lensR, lens);
  l.position.y = front - 0.004;
  sk.tilt.add(l);
  sk.emitters.push(emitter(sk.tilt, front, lensR));
  sk.height = axis + front + 0.15;
  sk.radius = 0.3;
  return sk;
}

function movingWash(body, family) {
  const sk = skeleton();
  const R = 0.165;
  movingBase(sk, body, 0.4, 0.15, 0.3);
  const axis = yoke(sk, body, R + 0.012, 0.26);
  const H = housing(body.style);
  const front = 0.13;
  const back = -0.16;
  const shell = lathe([
    [0.001, back], [R * 0.6, back + 0.004], [R * 0.95, back + 0.06],
    [R, 0], [R * 1.02, front - 0.02], [R * 0.98, front],
  ], H);
  sk.tilt.add(shell);
  const lens = lensMaterial();
  sk.lenses.push(lens);
  const fam = String(family || "");
  if (/spiider/.test(fam)) {
    const centre = disc(R * 0.36, lens);
    centre.position.y = front + 0.002;
    sk.tilt.add(centre);
    const { cells } = hexCells(19, R * 0.92);
    const ringGeo = new THREE.CircleGeometry(0.026, 16);
    ringGeo.rotateX(-Math.PI / 2);
    const ring = new THREE.InstancedMesh(ringGeo, lens, 18);
    const m = new THREE.Matrix4();
    for (let i = 0; i < 18; i++) {
      const a = (i / 18) * Math.PI * 2;
      m.makeTranslation(Math.cos(a) * R * 0.72, front + 0.002, Math.sin(a) * R * 0.72);
      ring.setMatrixAt(i, m);
    }
    sk.tilt.add(ring);
    void cells;
  } else {
    lensFace(sk.tilt, front + 0.001, R * 0.9, Math.max(7, Math.min(37, body.cells > 1 ? body.cells : 19)), lens);
  }
  sk.emitters.push(emitter(sk.tilt, front, R * 0.85));
  sk.height = axis + front + 0.2;
  sk.radius = 0.32;
  return sk;
}

function movingBar(body) {
  const sk = skeleton();
  movingBase(sk, body, 0.36, 0.13, 0.26);
  const len = 0.9;
  const axis = yoke(sk, body, len / 2 + 0.012, 0.22, 0.05, 0.1);
  const H = housing(body.style);
  const bar = rbox(len, 0.14, 0.12, 0.02, H);
  bar.position.y = 0;
  sk.tilt.add(bar);
  const lens = lensMaterial();
  sk.lenses.push(lens);
  const n = Math.max(4, Math.min(12, body.cells));
  const geo = new THREE.CircleGeometry(0.04, 18);
  geo.rotateX(-Math.PI / 2);
  const cells = new THREE.InstancedMesh(geo, lens, n);
  const m = new THREE.Matrix4();
  for (let i = 0; i < n; i++) {
    m.makeTranslation(-len / 2 + (i + 0.5) * len / n, 0.071, 0);
    cells.setMatrixAt(i, m);
  }
  sk.tilt.add(cells);
  for (const x of [-len / 3, 0, len / 3]) sk.emitters.push(emitter(sk.tilt, 0.072, 0.05, { x }));
  sk.emitters.forEach((e) => { e.node.position.x = e.x; });
  sk.height = axis + 0.3;
  sk.radius = 0.5;
  return sk;
}

/** A static light on a yoke bracket (PAR, profile, fresnel ...). */
function bracket(sk, body, halfW, axisH) {
  const S = steel();
  const foot = rbox(halfW * 2 + 0.06, 0.012, 0.06, 0.004, S);
  foot.position.y = 0.006;
  sk.pan.add(foot);
  for (const s of [-1, 1]) {
    const arm = rbox(0.012, axisH, 0.04, 0.004, S);
    arm.position.set(s * (halfW + 0.012), axisH / 2, 0);
    sk.pan.add(arm);
    const knob = cyl(0.022, 0.022, 0.02, trim(body.style), 16);
    knob.rotation.z = Math.PI / 2;
    knob.position.set(s * (halfW + 0.026), axisH, 0);
    sk.pan.add(knob);
  }
  sk.tilt.position.y = axisH;
}

function ledPar(body) {
  const sk = skeleton();
  const R = 0.11;
  bracket(sk, body, R + 0.01, 0.13);
  const H = housing(body.style);
  const shell = lathe([
    [0.001, -0.08], [R * 0.8, -0.078], [R, -0.05], [R, 0.06], [R * 1.04, 0.075], [R * 0.95, 0.08],
  ], H);
  sk.tilt.add(shell);
  for (let i = 0; i < 5; i++) {
    const fin = cyl(R * 1.03, R * 1.03, 0.006, steel());
    fin.position.y = -0.045 + i * 0.016;
    sk.tilt.add(fin);
  }
  const plate = logo(body, R * 1.2, R * 0.3);
  if (plate) {
    plate.rotation.set(0, Math.PI / 2, Math.PI / 2);
    plate.position.set(R + 0.002, 0.0, 0);
    sk.tilt.add(plate);
  }
  const lens = lensMaterial();
  sk.lenses.push(lens);
  lensFace(sk.tilt, 0.078, R * 0.88, body.cells > 1 ? body.cells : 12, lens);
  sk.emitters.push(emitter(sk.tilt, 0.08, R * 0.8));
  sk.height = 0.35;
  sk.radius = 0.16;
  return sk;
}

function parCan(body) {
  const sk = skeleton();
  const R = 0.12;
  bracket(sk, body, R + 0.01, 0.16);
  const H = housing({ ...body.style, finish: "metal" });
  const can = lathe([
    [0.001, -0.22], [R * 0.7, -0.2], [R * 0.95, -0.12], [R, 0.1], [R * 1.12, 0.12], [R * 1.1, 0.14], [R * 0.95, 0.14],
  ], H);
  sk.tilt.add(can);
  const lens = lensMaterial();
  sk.lenses.push(lens);
  const l = disc(R * 0.92, lens);
  l.position.y = 0.139;
  sk.tilt.add(l);
  sk.emitters.push(emitter(sk.tilt, 0.14, R * 0.85, { warm: true }));
  sk.height = 0.45;
  sk.radius = 0.2;
  return sk;
}

function profile(body) {
  const sk = skeleton();
  bracket(sk, body, 0.13, 0.19);
  const H = housing(body.style);
  const rear = lathe([
    [0.001, -0.25], [0.1, -0.245], [0.14, -0.2], [0.15, -0.05], [0.12, 0.0], [0.09, 0.02],
  ], H);
  sk.tilt.add(rear);
  const barrel = lathe([
    [0.075, 0.0], [0.075, 0.22], [0.085, 0.23], [0.085, 0.36], [0.095, 0.37], [0.09, 0.38],
  ], H);
  sk.tilt.add(barrel);
  for (let i = 0; i < 4; i++) {                        // shutter handles
    const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
    const h = rbox(0.02, 0.012, 0.06, 0.004, steel());
    h.position.set(Math.cos(a) * 0.095, 0.05, Math.sin(a) * 0.095);
    h.rotation.y = -a;
    sk.tilt.add(h);
  }
  const knob = cyl(0.016, 0.016, 0.03, trim(body.style), 12);
  knob.position.set(0, 0.29, 0.1);
  knob.rotation.x = Math.PI / 2;
  sk.tilt.add(knob);
  const plate = logo(body, 0.2, 0.05);
  if (plate) {
    plate.rotation.set(0, Math.PI / 2, Math.PI / 2);
    plate.position.set(0.151, -0.12, 0);
    sk.tilt.add(plate);
  }
  const lens = lensMaterial();
  sk.lenses.push(lens);
  const l = disc(0.078, lens);
  l.position.y = 0.379;
  sk.tilt.add(l);
  sk.emitters.push(emitter(sk.tilt, 0.38, 0.07));
  sk.height = 0.6;
  sk.radius = 0.3;
  return sk;
}

function fresnel(body) {
  const sk = skeleton();
  bracket(sk, body, 0.15, 0.18);
  const H = housing(body.style);
  const box = rbox(0.28, 0.26, 0.28, 0.04, H);
  box.position.y = -0.02;
  sk.tilt.add(box);
  const lens = lensMaterial();
  sk.lenses.push(lens);
  for (let i = 0; i < 4; i++) {                        // fresnel steps
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.03 + i * 0.026, 0.05 + i * 0.026, 36), lens);
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.111 + i * 0.001;
    sk.tilt.add(ring);
  }
  const centre = disc(0.032, lens);
  centre.position.y = 0.112;
  sk.tilt.add(centre);
  for (let i = 0; i < 4; i++) {                        // barn doors
    const door = new THREE.Mesh(new THREE.BoxGeometry(0.26, 0.004, 0.12), steel());
    const a = (i / 4) * Math.PI * 2;
    const g = new THREE.Group();
    g.rotation.y = a;
    door.position.set(0, 0.14, 0.18);
    door.rotation.x = 0.55;
    g.add(door);
    sk.tilt.add(g);
  }
  const plate = logo(body, 0.2, 0.05);
  if (plate) {
    plate.rotation.set(0, Math.PI / 2, Math.PI / 2);
    plate.position.set(0.141, -0.02, 0);
    sk.tilt.add(plate);
  }
  sk.emitters.push(emitter(sk.tilt, 0.115, 0.1));
  sk.height = 0.5;
  sk.radius = 0.3;
  return sk;
}

function panel(body, w, d, cells, cellR) {
  const sk = skeleton();
  bracket(sk, body, w / 2 + 0.01, 0.12);
  const H = housing(body.style);
  const box = rbox(w, 0.08, d, 0.012, H);
  sk.tilt.add(box);
  const lens = lensMaterial();
  sk.lenses.push(lens);
  const nx = Math.max(1, Math.round(Math.sqrt(cells * w / d)));
  const nz = Math.max(1, Math.ceil(cells / nx));
  const geo = new THREE.CircleGeometry(cellR, 14);
  geo.rotateX(-Math.PI / 2);
  const inst = new THREE.InstancedMesh(geo, lens, nx * nz);
  const m = new THREE.Matrix4();
  let k = 0;
  for (let i = 0; i < nx; i++) {
    for (let j = 0; j < nz; j++) {
      m.makeTranslation(-w / 2 + (i + 0.5) * w / nx, 0.041, -d / 2 + (j + 0.5) * d / nz);
      inst.setMatrixAt(k++, m);
    }
  }
  sk.tilt.add(inst);
  const plate = logo(body, w * 0.4, w * 0.1);
  if (plate) {
    plate.rotation.set(-Math.PI / 2, 0, 0);
    plate.position.set(0, -0.041, 0);
    plate.rotation.set(Math.PI / 2, 0, 0);
    sk.tilt.add(plate);
  }
  sk.emitters.push(emitter(sk.tilt, 0.042, Math.min(w, d) * 0.45));
  sk.height = 0.3;
  sk.radius = Math.max(w, d) * 0.6;
  return sk;
}

function batten(body) {
  const sk = skeleton();
  const n = Math.max(4, Math.min(32, body.cells || 8));
  const len = Math.max(0.5, Math.min(1.3, 0.11 * n + 0.08));
  const S = steel();
  for (const s of [-1, 1]) {                          // end brackets
    const b = rbox(0.012, 0.1, 0.07, 0.004, S);
    b.position.set(s * (len / 2 + 0.01), 0.05, 0);
    sk.pan.add(b);
  }
  sk.tilt.position.y = 0.08;
  const H = housing(body.style);
  const bar = rbox(len, 0.07, 0.08, 0.012, H);
  sk.tilt.add(bar);
  const lens = lensMaterial();
  sk.lenses.push(lens);
  const geo = new THREE.PlaneGeometry(len / n * 0.78, 0.055);
  geo.rotateX(-Math.PI / 2);
  const inst = new THREE.InstancedMesh(geo, lens, n);
  const m = new THREE.Matrix4();
  for (let i = 0; i < n; i++) {
    m.makeTranslation(-len / 2 + (i + 0.5) * len / n, 0.036, 0);
    inst.setMatrixAt(i, m);
  }
  sk.tilt.add(inst);
  const plate = logo(body, 0.2, 0.05);
  if (plate) {
    plate.position.set(len / 2 - 0.14, 0, 0.0405);
    sk.tilt.add(plate);
  }
  const beams = Math.min(4, Math.max(2, Math.round(len / 0.35)));
  for (let i = 0; i < beams; i++) {
    const e = emitter(sk.tilt, 0.037, 0.04);
    e.node.position.x = -len / 2 + (i + 0.5) * len / beams;
    sk.emitters.push(e);
  }
  sk.height = 0.2;
  sk.radius = len / 2 + 0.05;
  return sk;
}

function strobe(body, family) {
  const sk = skeleton();
  const jdc = /jdc/.test(String(family || ""));
  const w = jdc ? 0.95 : 0.52;
  bracket(sk, body, w / 2 + 0.01, 0.11);
  const H = housing(body.style);
  const box = rbox(w, 0.12, 0.16, 0.02, H);
  sk.tilt.add(box);
  const lens = lensMaterial();
  sk.lenses.push(lens);
  const tube = cyl(0.018, 0.018, w * 0.82, lens, 16);
  tube.rotation.z = Math.PI / 2;
  tube.position.y = 0.05;
  sk.tilt.add(tube);
  const refl = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.9, 0.12),
    new THREE.MeshStandardMaterial({ color: 0xb8bcc4, roughness: 0.15, metalness: 1 }));
  refl.rotation.x = -Math.PI / 2;
  refl.position.y = 0.035;
  sk.tilt.add(refl);
  const plate = logo(body, 0.22, 0.055);
  if (plate) {
    plate.position.set(-w / 2 + 0.15, 0, 0.081);
    sk.tilt.add(plate);
  }
  sk.emitters.push(emitter(sk.tilt, 0.061, 0.08, { flat: true }));
  sk.height = 0.3;
  sk.radius = w / 2 + 0.05;
  return sk;
}

function blinder(body) {
  const sk = skeleton();
  bracket(sk, body, 0.32, 0.12);
  const H = housing(body.style);
  const box = rbox(0.62, 0.1, 0.3, 0.02, H);
  sk.tilt.add(box);
  const lens = lensMaterial();
  sk.lenses.push(lens);
  for (const [x, z] of [[-0.15, -0.075], [0.15, -0.075], [-0.15, 0.075], [0.15, 0.075]]) {
    const cup = lathe([[0.001, 0.02], [0.05, 0.03], [0.065, 0.051]],
      new THREE.MeshStandardMaterial({ color: 0xd5d8de, roughness: 0.2, metalness: 1 }));
    cup.position.set(x, 0, z);
    sk.tilt.add(cup);
    const bulb = disc(0.045, lens);
    bulb.position.set(x, 0.049, z);
    sk.tilt.add(bulb);
  }
  sk.emitters.push(emitter(sk.tilt, 0.052, 0.2, { warm: true }));
  sk.height = 0.3;
  sk.radius = 0.35;
  return sk;
}

function tube(body) {
  const sk = skeleton();
  // The tube stands up; its light goes out sideways, so the tilt group is
  // turned to point +Y at the audience and the tube runs along local Z.
  const len = 1.0;
  const S = steel();
  const foot = cyl(0.07, 0.08, 0.02, S);
  foot.position.y = 0.01;
  sk.root.add(foot);
  sk.tilt.position.y = 0.02 + len / 2;
  const lens = lensMaterial();
  sk.lenses.push(lens);
  const t = cyl(0.022, 0.022, len, lens, 20);
  t.rotation.x = Math.PI / 2;
  sk.tilt.add(t);
  for (const s of [-1, 1]) {
    const cap = cyl(0.025, 0.025, 0.04, housing(body.style), 16);
    cap.rotation.x = Math.PI / 2;
    cap.position.z = s * (len / 2);
    sk.tilt.add(cap);
  }
  sk.emitters.push(emitter(sk.tilt, 0.025, 0.02, { flat: true }));
  sk.height = len + 0.05;
  sk.radius = 0.1;
  sk.standing = true;                  // the stage aims it outward, not up
  return sk;
}

function laser(body) {
  const sk = skeleton();
  bracket(sk, body, 0.12, 0.09);
  const H = housing(body.style);
  const box = rbox(0.22, 0.16, 0.26, 0.02, H);
  box.rotation.x = Math.PI / 2;
  sk.tilt.add(box);
  const lens = lensMaterial();
  sk.lenses.push(lens);
  const ap = disc(0.012, lens);
  ap.position.y = 0.131;
  sk.tilt.add(ap);
  sk.emitters.push(emitter(sk.tilt, 0.132, 0.004, { laser: true }));
  sk.height = 0.3;
  sk.radius = 0.2;
  return sk;
}

function followspot(body) {
  const sk = skeleton();
  const S = steel();
  const top = new THREE.Vector3(0, 1.35, 0);
  for (let i = 0; i < 3; i++) {                        // tripod
    const a = (i / 3) * Math.PI * 2;
    const foot = new THREE.Vector3(Math.cos(a) * 0.45, 0, Math.sin(a) * 0.45);
    const along = top.clone().sub(foot);
    const leg = cyl(0.012, 0.012, along.length(), S, 8);
    leg.position.copy(foot).addScaledVector(along, 0.5);
    leg.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), along.normalize());
    sk.root.add(leg);
  }
  const post = cyl(0.02, 0.02, 0.1, S, 12);
  post.position.y = 1.4;
  sk.root.add(post);
  sk.pan.position.y = 1.4;
  sk.tilt.position.y = 0.1;
  const H = housing(body.style);
  const barrel = lathe([[0.001, -0.5], [0.14, -0.48], [0.15, 0.2], [0.11, 0.35], [0.11, 0.6]], H);
  sk.tilt.add(barrel);
  const lens = lensMaterial();
  sk.lenses.push(lens);
  const l = disc(0.1, lens);
  l.position.y = 0.6;
  sk.tilt.add(l);
  sk.emitters.push(emitter(sk.tilt, 0.6, 0.09));
  sk.height = 1.7;
  sk.radius = 0.4;
  return sk;
}

function atmos(body) {
  const sk = skeleton();
  const H = housing(body.style);
  const box = rbox(0.5, 0.26, 0.3, 0.03, H);
  box.position.y = 0.13;
  sk.root.add(box);
  const nozzle = cyl(0.03, 0.04, 0.08, steel(), 16);
  nozzle.rotation.x = Math.PI / 2;
  nozzle.position.set(0, 0.15, 0.18);
  sk.root.add(nozzle);
  const plate = logo(body, 0.26, 0.065);
  if (plate) {
    plate.position.set(0, 0.2, 0.151);
    sk.root.add(plate);
  }
  sk.height = 0.3;
  sk.radius = 0.3;
  return sk;
}

function generic(body) {
  const sk = ledPar({ ...body, cells: 1 });
  return sk;
}

const BUILDERS = {
  moving_spot: (b) => movingSpot(b, "spot"),
  moving_hybrid: (b) => movingSpot(b, "hybrid"),
  moving_beam: (b) => movingSpot(b, "beam"),
  moving_wash: (b, fam) => movingWash(b, fam),
  moving_bar: (b) => movingBar(b),
  par: ledPar,
  par_can: parCan,
  profile,
  fresnel,
  wash_panel: (b) => panel(b, 0.42, 0.3, Math.max(12, b.cells || 24), 0.018),
  matrix: (b) => panel(b, 0.4, 0.4, 25, 0.03),
  cyc: (b) => panel(b, 0.36, 0.24, 8, 0.03),
  bar: batten,
  strobe: (b, fam) => strobe(b, fam),
  blinder,
  tube,
  laser,
  followspot,
  atmos,
  generic,
};

/** Build the 3D model for one head from its physical description. */
export function buildFixture(body, family = "") {
  const b = body || { type: "generic", style: { body: "#26282c", accent: "#9aa4b2", finish: "matte" }, cells: 1 };
  const make = BUILDERS[b.type] || BUILDERS.generic;
  const sk = make(b, family.toLowerCase());
  sk.root.traverse((o) => {
    if (o.isMesh) { o.castShadow = false; o.receiveShadow = false; }
  });
  return sk;
}
