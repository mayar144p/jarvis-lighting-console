// The room the rig lives in: deck, cyc, masking, house floor, the truss the
// hung lights are on, and a few performers for scale.  Every surface uses
// the beam-lit material, so each light lands where it really would.
import * as THREE from "three";
import { surfaceMaterial } from "./materials.js";

/** Stage size: the stored venue, or grown around the patch. */
export function roomFor(venue, fixtures) {
  let w = Number(venue && venue.width_m) || 0;
  let d = Number(venue && venue.depth_m) || 0;
  let h = Number(venue && venue.height_m) || 0;
  let spanX = 0, spanZ = 0, top = 0;
  for (const f of fixtures) {
    spanX = Math.max(spanX, Math.abs(f.x || 0));
    spanZ = Math.max(spanZ, f.z || 0);
    top = Math.max(top, f.y || 0);
  }
  w = Math.max(w, spanX * 2 + 3, 10);
  d = Math.max(d, spanZ + 2, 8);
  h = Math.max(h, top + 1.5, 7);
  return { w, d, h };
}

function lerp(a, b, t) { return a + (b - a) * t; }

function plane(w, h, mat) {
  return new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
}

/** Box truss between two points (0.3 m, four chords and lacing). */
export function trussSegment(a, b, size = 0.3, mat) {
  const g = new THREE.Group();
  const dir = new THREE.Vector3().subVectors(b, a);
  const len = dir.length();
  if (len < 0.05) return g;
  const chordGeo = new THREE.CylinderGeometry(0.024, 0.024, len, 8);
  const h = size / 2;
  for (const [x, z] of [[-h, -h], [h, -h], [-h, h], [h, h]]) {
    const c = new THREE.Mesh(chordGeo, mat);
    c.position.set(x, len / 2, z);
    g.add(c);
  }
  const bays = Math.max(1, Math.round(len / size));
  const step = len / bays;
  const diag = Math.hypot(step, size);
  const lacingGeo = new THREE.CylinderGeometry(0.009, 0.009, diag, 6);
  const faces = [[-h, 0, 0], [h, 0, 0], [0, 0, -h], [0, 0, h]];
  for (let i = 0; i < bays; i++) {
    faces.forEach(([fx, , fz], k) => {
      const l = new THREE.Mesh(lacingGeo, mat);
      const flip = (i + k) % 2 ? 1 : -1;
      l.position.set(fx, (i + 0.5) * step, fz);
      // a diagonal lies IN its face: tilt it along the face's own width
      if (fx) l.rotation.x = flip * Math.atan2(size, step);
      else l.rotation.z = flip * Math.atan2(size, step);
      g.add(l);
    });
  }
  g.position.copy(a);
  g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
  return g;
}

/** A simple standing figure, lit by the rig like everything else. */
function performer(mat, height = 1.75) {
  const g = new THREE.Group();
  const s = height / 1.75;
  const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.17 * s, 0.5 * s, 6, 12), mat);
  torso.position.y = 1.18 * s;
  g.add(torso);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.11 * s, 16, 12), mat);
  head.position.y = 1.64 * s;
  g.add(head);
  for (const sx of [-1, 1]) {
    const leg = new THREE.Mesh(new THREE.CapsuleGeometry(0.075 * s, 0.72 * s, 4, 8), mat);
    leg.position.set(sx * 0.09 * s, 0.44 * s, 0);
    g.add(leg);
    const arm = new THREE.Mesh(new THREE.CapsuleGeometry(0.05 * s, 0.52 * s, 4, 8), mat);
    arm.position.set(sx * 0.25 * s, 1.15 * s, 0);
    arm.rotation.z = sx * 0.12;
    g.add(arm);
  }
  return g;
}

/**
 * Build the venue.  Returns {group, room, planes} where `planes` are the
 * surfaces a beam can end on (for the beam length).
 */
export function buildVenue(venue, fixtures, opts = {}) {
  const room = roomFor(venue, fixtures);
  const { w, d, h } = room;
  const group = new THREE.Group();
  const deckMat = surfaceMaterial(0x2a2b30, { grid: 0.35, sheen: 1 });
  const cycMat = surfaceMaterial(0x9ea3ad);
  const maskMat = surfaceMaterial(0x0c0c0e);
  const houseMat = surfaceMaterial(0x121317);
  const crowdMat = surfaceMaterial(0x3a3d45);

  const deck = plane(w, d + 0.6, deckMat);
  deck.rotation.x = -Math.PI / 2;
  deck.position.set(0, 0, (d - 0.6) / 2);
  group.add(deck);
  const lip = new THREE.Mesh(new THREE.BoxGeometry(w, 0.04, 0.06), maskMat);
  lip.position.set(0, 0.02, d);
  group.add(lip);

  const house = plane(w + 16, 18, houseMat);
  house.rotation.x = -Math.PI / 2;
  house.position.set(0, -0.005, d + 9);
  group.add(house);

  const cyc = plane(w + 2, h + 1, cycMat);
  cyc.position.set(0, (h + 1) / 2, -0.6);
  group.add(cyc);

  for (const s of [-1, 1]) {
    const leg = plane(d + 0.6, h + 1, maskMat);
    leg.rotation.y = s * -Math.PI / 2;
    leg.position.set(s * (w / 2 + 1), (h + 1) / 2, (d - 0.6) / 2);
    group.add(leg);
  }

  // the stored venue's own lines (walls, truss) are drawn as they were drawn
  const trussMat = new THREE.MeshStandardMaterial({
    color: 0x6f757e, roughness: 0.55, metalness: 0.8, envMapIntensity: 0.07,
  });
  const chainMat = new THREE.MeshStandardMaterial({
    color: 0x1a1b1e, roughness: 0.6, metalness: 0.6, envMapIntensity: 0.15,
  });
  for (const s of (venue && venue.surfaces) || []) {
    const a = new THREE.Vector3(s.x1, s.y1, s.z1);
    const b = new THREE.Vector3(s.x2, s.y2, s.z2);
    if (String(s.kind).toLowerCase() === "truss") {
      group.add(trussSegment(a, b, 0.3, trussMat));
    } else {
      const len = a.distanceTo(b);
      const wall = new THREE.Mesh(new THREE.BoxGeometry(len, Math.max(0.1, h * 0.6), 0.08),
        surfaceMaterial(new THREE.Color(s.color || "#64748b").getHex()));
      wall.position.copy(a).add(b).multiplyScalar(0.5);
      wall.position.y = Math.max(0.05, h * 0.3);
      wall.rotation.y = -Math.atan2(b.z - a.z, b.x - a.x);
      group.add(wall);
    }
  }

  // one truss per row of hung lights, on two towers
  const rows = new Map();
  for (const f of fixtures) {
    if ((f.kind || (f.y >= 2 ? "truss" : "floor")) !== "truss") continue;
    const key = Math.round(f.y * 2) / 2 + "|" + Math.round(f.z * 2) / 2;
    const r = rows.get(key) || { y: 0, z: 0, n: 0, x0: Infinity, x1: -Infinity };
    r.y += f.y; r.z += f.z; r.n++;
    r.x0 = Math.min(r.x0, f.x); r.x1 = Math.max(r.x1, f.x);
    rows.set(key, r);
  }
  for (const r of rows.values()) {
    const y = r.y / r.n + 0.18, z = r.z / r.n;
    const half = Math.max(Math.abs(r.x0), Math.abs(r.x1)) + 1.0;
    const x0 = -Math.min(half, w / 2 + 0.6), x1 = Math.min(half, w / 2 + 0.6);
    group.add(trussSegment(new THREE.Vector3(x0 - 0.15, y, z), new THREE.Vector3(x1 + 0.15, y, z), 0.3, trussMat));
    // Flown, not ground-supported: a chain hoist near each end and one
    // every ~4 m, up into the grid.  Towers in front of the stage would
    // stand between the audience and the show.
    const picks = Math.max(2, Math.round((x1 - x0) / 4) + 1);
    for (let i = 0; i < picks; i++) {
      const x = lerp(x0 + 0.4, x1 - 0.4, picks === 1 ? 0.5 : i / (picks - 1));
      const top = h + 1;
      const chain = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, top - y - 0.35, 5), chainMat);
      chain.position.set(x, (top + y + 0.35) / 2, z);
      group.add(chain);
      const hoist = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.28, 0.18), chainMat);
      hoist.position.set(x, y + 0.62, z);
      group.add(hoist);
    }
  }

  const people = new THREE.Group();
  const spots = [[-1.6, 0.55], [0.2, 0.62], [1.9, 0.5]];
  spots.forEach(([x, zf], i) => {
    const p = performer(crowdMat, 1.7 + i * 0.05);
    p.position.set(x, 0, d * zf);
    p.rotation.y = (i - 1) * 0.3;
    people.add(p);
  });
  people.visible = opts.people !== false;
  group.add(people);

  const planes = [
    { n: new THREE.Vector3(0, 1, 0), c: 0 },                 // floor
    { n: new THREE.Vector3(0, 0, 1), c: -0.6 },              // cyc
    { n: new THREE.Vector3(1, 0, 0), c: -(w / 2 + 1) },      // stage-left leg
    { n: new THREE.Vector3(-1, 0, 0), c: -(w / 2 + 1) },     // stage-right leg
    { n: new THREE.Vector3(0, -1, 0), c: -(h + 1) },         // grid
  ];
  return { group, room, planes, people };
}

/** Distance along a ray to the first surface it meets (capped). */
export function hitDistance(planes, origin, dir, cap = 24) {
  let best = cap;
  for (const p of planes) {
    const denom = p.n.dot(dir);
    if (denom >= -1e-4) continue;                  // facing away
    const t = (p.c - p.n.dot(origin)) / denom;
    if (t > 0.02 && t < best) best = t;
  }
  return best;
}
