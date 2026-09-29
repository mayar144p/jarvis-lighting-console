// The room the rig lives in, built from the engine's venue document
// (app/venue.py): floor, walls, ceiling, stage, rigging, objects, zones,
// the crowd and a traced floor plan.
//
// Everything a beam can land on uses the beam-lit surface material, so a
// light pools on the floor, climbs a wall and catches the crowd where it
// really would.  Static geometry is merged per item so a 12 m truss is one
// draw call, not three hundred.
import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { surfaceMaterial, crowdMaterial } from "./materials.js";

const DEG = Math.PI / 180;

const FLOOR_COLOURS = {
  concrete: 0x3b3d42, wood: 0x4a3a2c, grass: 0x2c3a24, black: 0x17181b,
  carpet: 0x2d2a33, tiles: 0x4a4c52,
};

// ---------------------------------------------------------------------------
// the room when nothing was drawn: sized around the patch
// ---------------------------------------------------------------------------
export function autoVenue(fixtures) {
  let spanX = 0, spanZ = 0, top = 0;
  for (const f of fixtures) {
    spanX = Math.max(spanX, Math.abs(f.x || 0));
    spanZ = Math.max(spanZ, f.z || 0);
    top = Math.max(top, f.y || 0);
  }
  const sw = Math.max(10, spanX * 2 + 3);
  const sd = Math.max(8, spanZ + 2);
  // one truss per row of hung lights, so nothing floats
  const rows = new Map();
  for (const f of fixtures) {
    const hung = f.stance ? f.stance === "hang" : (f.kind || (f.y >= 2 ? "truss" : "floor")) === "truss";
    if (!hung) continue;
    const key = Math.round(f.y * 2) / 2 + "|" + Math.round(f.z * 2) / 2;
    const r = rows.get(key) || { y: 0, z: 0, n: 0, x0: Infinity, x1: -Infinity };
    r.y += f.y; r.z += f.z; r.n++;
    r.x0 = Math.min(r.x0, f.x); r.x1 = Math.max(r.x1, f.x);
    rows.set(key, r);
  }
  const rigging = [...rows.values()].map((r, i) => {
    const y = r.y / r.n + 0.2, z = r.z / r.n;
    return { id: "auto" + i, kind: "truss", name: "", size: 0.3,
      a: [r.x0 - 0.8, y, z], b: [r.x1 + 0.8, y, z] };
  });
  return {
    auto: true,
    room: { width: sw + 6, depth: sd + 14, height: Math.max(7, top + 1.5), back: -1.5,
      ceiling: "open", floor: "concrete", wall_colour: "#24262c", outline: [] },
    stage: { x: 0, z: 0, width: sw, depth: sd, height: 0, colour: "#26272c" },
    rigging, objects: [], zones: [
      { id: "autoz", kind: "standing", points: [[-sw / 2, sd + 1.5], [sw / 2, sd + 1.5], [sw / 2, sd + 10], [-sw / 2, sd + 10]], y: 0, density: 0.5 },
    ], cameras: [],
    crowd: { style: "varied", density: 0.4, show: true }, underlay: null,
  };
}

/** The venue to draw: the stored one, or a room around the patch. */
export function effectiveVenue(venue, fixtures) {
  const r = venue && venue.room;
  if (!venue || venue.auto || !r || !r.width || !r.depth) return autoVenue(fixtures);
  return venue;
}

export function roomBox(v) {
  const r = v.room;
  const back = r.back ?? -1;
  return { x0: -r.width / 2, x1: r.width / 2, z0: back, z1: back + r.depth, h: r.height || 6,
    w: r.width, d: r.depth };
}

// ---------------------------------------------------------------------------
// geometry helpers
// ---------------------------------------------------------------------------
function baked(geo, pos, quat, scale) {
  const m = new THREE.Matrix4().compose(pos || new THREE.Vector3(),
    quat || new THREE.Quaternion(), scale || new THREE.Vector3(1, 1, 1));
  const g = geo.index ? geo.toNonIndexed() : geo.clone();
  g.applyMatrix4(m);
  if (!g.attributes.uv) {
    g.setAttribute("uv", new THREE.BufferAttribute(new Float32Array(g.attributes.position.count * 2), 2));
  }
  return g;
}

function merged(parts, mat) {
  const list = parts.filter(Boolean);
  if (!list.length) return null;
  const mesh = new THREE.Mesh(mergeGeometries(list, false), mat);
  for (const p of list) p.dispose();
  return mesh;
}

const _up = new THREE.Vector3(0, 1, 0);

/** A bar between two points as a cylinder geometry, baked in place. */
function rod(a, b, r, seg = 8) {
  const dir = new THREE.Vector3().subVectors(b, a);
  const len = dir.length();
  if (len < 1e-4) return null;
  const g = new THREE.CylinderGeometry(r, r, len, seg, 1, false);
  const q = new THREE.Quaternion().setFromUnitVectors(_up, dir.normalize());
  return baked(g, new THREE.Vector3().addVectors(a, b).multiplyScalar(0.5), q);
}

function boxGeo(w, h, d, x, y, z, rotY = 0) {
  const q = new THREE.Quaternion().setFromAxisAngle(_up, rotY);
  return baked(new THREE.BoxGeometry(w, h, d), new THREE.Vector3(x, y, z), q);
}

/** Box truss (four chords and zig-zag lacing) from a to b, merged. */
function trussGeo(a, b, size, triangle = false) {
  const dir = new THREE.Vector3().subVectors(b, a);
  const len = dir.length();
  if (len < 0.05) return [];
  const q = new THREE.Quaternion().setFromUnitVectors(_up, dir.clone().normalize());
  const h = size / 2;
  const corners = triangle ? [[-h, -h * 0.58], [h, -h * 0.58], [0, h * 1.15]]
    : [[-h, -h], [h, -h], [h, h], [-h, h]];
  const local = [];
  for (const [x, z] of corners) {
    local.push(rod(new THREE.Vector3(x, 0, z), new THREE.Vector3(x, len, z), 0.024, 6));
  }
  const bays = Math.max(1, Math.round(len / size));
  const step = len / bays;
  for (let i = 0; i < bays; i++) {
    for (let k = 0; k < corners.length; k++) {
      const c0 = corners[k], c1 = corners[(k + 1) % corners.length];
      const y0 = i * step, y1 = (i + 1) * step;
      const flip = (i + k) % 2;
      local.push(rod(new THREE.Vector3(c0[0], flip ? y0 : y1, c0[1]),
        new THREE.Vector3(c1[0], flip ? y1 : y0, c1[1]), 0.009, 4));
    }
  }
  return local.filter(Boolean).map((g) => { g.applyQuaternion(q); g.translate(a.x, a.y, a.z); return g; });
}

// ---------------------------------------------------------------------------
// materials (shared per build)
// ---------------------------------------------------------------------------
function mats(v) {
  const steel = new THREE.MeshStandardMaterial({ color: 0x8a9099, roughness: 0.45, metalness: 0.85, envMapIntensity: 0.35 });
  const black = new THREE.MeshStandardMaterial({ color: 0x141518, roughness: 0.7, metalness: 0.3, envMapIntensity: 0.2 });
  return {
    floor: surfaceMaterial(FLOOR_COLOURS[v.room.floor] ?? FLOOR_COLOURS.concrete, { grid: 0.22, sheen: 1 }),
    wall: surfaceMaterial(new THREE.Color(v.room.wall_colour || "#2a2c33").getHex()),
    ceiling: surfaceMaterial(0x24262b, { side: THREE.DoubleSide }),
    deck: surfaceMaterial(new THREE.Color((v.stage && v.stage.colour) || "#26272c").getHex(), { grid: 0.12, sheen: 0.6 }),
    skirt: surfaceMaterial(0x0e0f12),
    object: surfaceMaterial(0x2e3036),
    wood: surfaceMaterial(0x5a4430),
    screen: surfaceMaterial(0x0b0c10, { sheen: 1 }),
    steel, black,
    chain: new THREE.MeshStandardMaterial({ color: 0x1a1b1e, roughness: 0.6, metalness: 0.6, envMapIntensity: 0.15 }),
    edge: new THREE.LineBasicMaterial({ color: 0x5b6475, transparent: true, opacity: 0.55, toneMapped: false }),
  };
}

// ---------------------------------------------------------------------------
// rigging
// ---------------------------------------------------------------------------
function buildRig(r, M, room) {
  const a = new THREE.Vector3(...r.a), b = new THREE.Vector3(...r.b);
  const g = new THREE.Group();
  g.userData = { venueId: r.id, venueKind: r.kind };
  const vertical = Math.abs(b.y - a.y) > Math.max(Math.abs(b.x - a.x), Math.abs(b.z - a.z));
  const parts = [];
  const dark = [];
  if (r.kind === "truss" || r.kind === "tower") {
    parts.push(...trussGeo(a, b, r.size));
    if (vertical) {
      const lo = a.y < b.y ? a : b;
      dark.push(boxGeo(r.size * 2.2, 0.03, r.size * 2.2, lo.x, lo.y + 0.015, lo.z));
    } else {
      // flown: a chain hoist near each end and one every ~4 m
      const top = room.h;
      const picks = Math.max(2, Math.round(a.distanceTo(b) / 4) + 1);
      for (let i = 0; i < picks; i++) {
        const t = picks === 1 ? 0.5 : 0.06 + 0.88 * (i / (picks - 1));
        const p = new THREE.Vector3().lerpVectors(a, b, t);
        if (top - p.y > 0.6) {
          dark.push(rod(new THREE.Vector3(p.x, p.y + 0.5, p.z), new THREE.Vector3(p.x, top, p.z), 0.008, 4));
          dark.push(boxGeo(0.2, 0.28, 0.18, p.x, p.y + 0.36, p.z));
        }
      }
    }
  } else if (r.kind === "ladder") {
    const n = new THREE.Vector3(1, 0, 0).multiplyScalar(r.size / 2);
    parts.push(rod(a.clone().add(n), b.clone().add(n), 0.022));
    parts.push(rod(a.clone().sub(n), b.clone().sub(n), 0.022));
    const rungs = Math.max(2, Math.round(a.distanceTo(b) / 0.3));
    for (let i = 0; i <= rungs; i++) {
      const p = new THREE.Vector3().lerpVectors(a, b, i / rungs);
      parts.push(rod(p.clone().add(n), p.clone().sub(n), 0.012, 6));
    }
  } else if (r.kind === "pipe") {
    parts.push(rod(a, b, Math.max(0.024, r.size / 2), 12));
    const top = room.h;
    for (const t of [0.08, 0.92]) {
      const p = new THREE.Vector3().lerpVectors(a, b, t);
      if (top - p.y > 0.1) dark.push(rod(new THREE.Vector3(p.x, p.y, p.z), new THREE.Vector3(p.x, top, p.z), 0.006, 4));
    }
  } else if (r.kind === "stand") {
    const lo = a.y < b.y ? a : b, hi = a.y < b.y ? b : a;
    parts.push(rod(lo, hi, 0.022, 10));
    for (let k = 0; k < 3; k++) {
      const ang = k * (Math.PI * 2 / 3);
      const foot = new THREE.Vector3(lo.x + Math.cos(ang) * 0.55, lo.y, lo.z + Math.sin(ang) * 0.55);
      dark.push(rod(new THREE.Vector3(lo.x, lo.y + 0.5, lo.z), foot, 0.014, 6));
    }
    parts.push(rod(new THREE.Vector3(hi.x - 0.45, hi.y, hi.z), new THREE.Vector3(hi.x + 0.45, hi.y, hi.z), 0.02, 8));
  } else if (r.kind === "base") {
    dark.push(boxGeo(r.size, 0.025, r.size, a.x, a.y + 0.0125, a.z));
  }
  const m1 = merged(parts, M.steel);
  const m2 = merged(dark, M.chain);
  if (m1) g.add(m1);
  if (m2) g.add(m2);
  g.traverse((o) => { if (o.isMesh) o.userData = { venueId: r.id, venueKind: r.kind }; });
  return g;
}

// ---------------------------------------------------------------------------
// objects
// ---------------------------------------------------------------------------
function buildObject(o, M) {
  const g = new THREE.Group();
  g.position.set(o.x, o.y || 0, o.z);
  g.rotation.y = -(o.rot || 0) * DEG;
  const add = (geo, mat) => { const m = new THREE.Mesh(geo, mat); g.add(m); return m; };
  const { w, d, h } = o;
  const custom = o.colour ? surfaceMaterial(new THREE.Color(o.colour).getHex()) : null;
  switch (o.kind) {
    case "dj_booth": {
      add(new THREE.BoxGeometry(w, h, d).translate(0, h / 2, 0), custom || M.object);
      add(new THREE.BoxGeometry(w * 0.98, 0.02, d * 0.98).translate(0, h + 0.01, 0), M.black);
      for (const sx of [-0.3, 0, 0.3]) {
        add(new THREE.BoxGeometry(w * 0.22, 0.06, d * 0.55).translate(sx * w, h + 0.05, 0), M.black);
      }
      break;
    }
    case "bar": {
      add(new THREE.BoxGeometry(w, h, d).translate(0, h / 2, 0), custom || M.wood);
      add(new THREE.BoxGeometry(w * 1.02, 0.05, d * 1.15).translate(0, h + 0.025, 0), M.black);
      break;
    }
    case "speaker": case "sub": {
      add(new THREE.BoxGeometry(w, h, d).translate(0, h / 2, 0), custom || M.black);
      const face = add(new THREE.PlaneGeometry(w * 0.9, h * 0.94).translate(0, h / 2, d / 2 + 0.003), M.skirt);
      face.userData.face = true;
      break;
    }
    case "pillar": {
      add(new THREE.CylinderGeometry(w / 2, w / 2, h, 24).translate(0, h / 2, 0), custom || M.wall);
      break;
    }
    case "screen": {
      add(new THREE.BoxGeometry(w, h, Math.max(0.05, d)).translate(0, h / 2, 0), M.black);
      add(new THREE.PlaneGeometry(w * 0.96, h * 0.94).translate(0, h / 2, Math.max(0.05, d) / 2 + 0.003), custom || M.screen);
      break;
    }
    case "table": {
      add(new THREE.CylinderGeometry(w / 2, w / 2, 0.04, 28).translate(0, h, 0), custom || M.object);
      add(new THREE.CylinderGeometry(0.05, 0.05, h, 8).translate(0, h / 2, 0), M.black);
      break;
    }
    case "balcony": {
      add(new THREE.BoxGeometry(w, 0.25, d).translate(0, -0.125, 0), custom || M.object);
      add(new THREE.BoxGeometry(w, 1.0, 0.06).translate(0, 0.5, d / 2), M.black);
      break;
    }
    case "mark": {
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.2, 0.26, 32).rotateX(-Math.PI / 2).translate(0, 0.006, 0),
        new THREE.MeshBasicMaterial({ color: 0xfbbf24, toneMapped: false, transparent: true, opacity: 0.85 }));
      g.add(ring);
      break;
    }
    default: {                                         // riser, wall, door
      add(new THREE.BoxGeometry(w, h, d).translate(0, h / 2, 0), custom || (o.kind === "wall" ? M.wall : M.object));
    }
  }
  g.traverse((m) => { if (m.isMesh) m.userData = { ...m.userData, venueId: o.id, venueKind: o.kind }; });
  g.userData = { venueId: o.id, venueKind: o.kind };
  return g;
}

/** Solid boxes a beam stops on: the stage deck and the bigger objects. */
function blockers(v) {
  const out = [];
  const s = v.stage;
  if (s && s.height > 0.01) {
    out.push(new THREE.Box3(new THREE.Vector3(s.x - s.width / 2, 0, s.z), new THREE.Vector3(s.x + s.width / 2, s.height, s.z + s.depth)));
  }
  for (const o of v.objects || []) {
    if (o.kind === "mark") continue;
    const y0 = o.kind === "balcony" ? o.y - 0.25 : o.y || 0;
    const y1 = o.kind === "balcony" ? o.y : (o.y || 0) + o.h;
    const r = Math.hypot(o.w, o.d) / 2;
    const rotated = Math.abs((o.rot || 0) % 180) > 1;
    const hw = rotated ? r : o.w / 2, hd = rotated ? r : o.d / 2;
    out.push(new THREE.Box3(new THREE.Vector3(o.x - hw, y0, o.z - hd), new THREE.Vector3(o.x + hw, y1, o.z + hd)));
  }
  return out;
}

// ---------------------------------------------------------------------------
// people: marks get a performer, zones get a crowd
// ---------------------------------------------------------------------------
function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return ((s >>> 0) % 100000) / 100000; };
}

function personGeo(pose, seated = false) {
  // proportions for a 1 m tall figure, scaled per instance
  const parts = [];
  const leg = seated ? 0.26 : 0.46;
  const hip = seated ? 0.27 : 0.5;
  parts.push(baked(new THREE.CapsuleGeometry(0.1, 0.3, 2, 7), new THREE.Vector3(0, hip + 0.2, 0)));
  const cloth = mergeGeometries(parts.splice(0), false);
  const arms = [];
  for (const sx of [-1, 1]) {
    const up = pose === 2 || (pose === 1 && sx > 0);
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), up ? sx * 2.75 : sx * 0.16);
    const y = up ? hip + 0.55 : hip + 0.22;
    arms.push(baked(new THREE.CapsuleGeometry(0.035, 0.28, 1, 5), new THREE.Vector3(sx * (up ? 0.12 : 0.14), y, 0), q));
  }
  const clothes = mergeGeometries([cloth, ...arms], false);
  const skin = [];
  skin.push(baked(new THREE.SphereGeometry(0.075, 8, 6), new THREE.Vector3(0, hip + 0.49, 0)));
  for (const sx of [-1, 1]) {
    if (seated) {
      skin.push(baked(new THREE.CapsuleGeometry(0.05, 0.2, 1, 5), new THREE.Vector3(sx * 0.06, hip, 0.12),
        new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), Math.PI / 2)));
      skin.push(baked(new THREE.CapsuleGeometry(0.045, leg - 0.05, 1, 5), new THREE.Vector3(sx * 0.06, leg / 2, 0.24)));
    } else {
      skin.push(baked(new THREE.CapsuleGeometry(0.05, leg - 0.06, 1, 5), new THREE.Vector3(sx * 0.06, leg / 2, 0)));
    }
  }
  return { clothes, skin: mergeGeometries(skin, false) };
}

const TOPS = [0x1f2937, 0x111827, 0xf3f4f6, 0x7f1d1d, 0x1e3a8a, 0x065f46, 0x6b21a8, 0x9a3412, 0x374151, 0xd4d4d8, 0x0f172a, 0xbe185d];
const SKIN = [0x8d5524, 0xc68642, 0xe0ac69, 0xf1c27d, 0xffdbac, 0x5c3a21];

/** Spots for people inside a polygon, about `perM2` per square metre. */
function scatter(points, perM2, rand, seated) {
  const xs = points.map((p) => p[0]), zs = points.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), z0 = Math.min(...zs), z1 = Math.max(...zs);
  const inside = (x, z) => {
    let c = false;
    for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
      const [xi, zi] = points[i], [xj, zj] = points[j];
      if ((zi > z) !== (zj > z) && x < (xj - xi) * (z - zi) / ((zj - zi) || 1e-9) + xi) c = !c;
    }
    return c;
  };
  const out = [];
  if (perM2 <= 0) return out;
  if (seated) {
    for (let z = z0 + 0.5; z < z1 - 0.2; z += 0.95) {
      for (let x = x0 + 0.4; x < x1 - 0.2; x += 0.6) {
        if (inside(x, z) && rand() < perM2 / 1.8) out.push([x, z]);
      }
    }
    return out;
  }
  const cell = 1 / Math.sqrt(perM2);
  for (let z = z0 + cell / 2; z < z1; z += cell) {
    for (let x = x0 + cell / 2; x < x1; x += cell) {
      const jx = x + (rand() - 0.5) * cell * 0.8, jz = z + (rand() - 0.5) * cell * 0.8;
      if (inside(jx, jz)) out.push([jx, jz]);
    }
  }
  return out;
}

const ZONE_CROWD = { dancefloor: 2.2, standing: 2.0, seating: 1.5, bar: 1.2, vip: 1.0, foh: 0.3, dj: 0, backstage: 0, stage: 0 };

function buildCrowd(v, stageFront) {
  const crowd = v.crowd || {};
  const group = new THREE.Group();
  if (crowd.show === false) return group;
  const varied = crowd.style !== "simple";
  const rand = rng(1234567);
  const spots = [[], [], []];                        // per pose
  const seatedSpots = [];
  for (const z of v.zones || []) {
    const base = ZONE_CROWD[z.kind] ?? 1;
    const dens = (z.density >= 0 ? z.density : 0.5) * (crowd.density ?? 0.55) * 2 * base;
    const seated = z.kind === "seating";
    for (const [x, zz] of scatter(z.points, dens, rand, seated)) {
      const s = { x, z: zz, y: z.y || 0 };
      if (seated) seatedSpots.push(s);
      else spots[varied ? (z.kind === "dancefloor" ? Math.floor(rand() * 3) : (rand() < 0.15 ? 1 : 0)) : 0].push(s);
    }
  }
  const lookAt = new THREE.Vector3(0, 0, Math.max(0, stageFront - 2));
  const place = (list, geo, seated) => {
    if (!list.length) return;
    for (const [part, palette] of [["clothes", TOPS], ["skin", SKIN]]) {
      const mesh = new THREE.InstancedMesh(geo[part], crowdMaterial(varied ? 0xffffff : 0x565b66), list.length);
      const m = new THREE.Matrix4(), q = new THREE.Quaternion(), sc = new THREE.Vector3(), p = new THREE.Vector3();
      const c = new THREE.Color();
      const r2 = rng(99 + list.length + (seated ? 7 : 0));
      list.forEach((s, i) => {
        const height = seated ? 1.25 : 1.55 + r2() * 0.4;
        const yaw = Math.atan2(lookAt.x - s.x, lookAt.z - s.z) + (r2() - 0.5) * 0.7;
        q.setFromAxisAngle(_up, yaw);
        sc.set(height * (0.9 + r2() * 0.2), height, height);
        p.set(s.x, s.y, s.z);
        m.compose(p, q, sc);
        mesh.setMatrixAt(i, m);
        if (varied) {
          const col = part === "clothes" ? palette[Math.floor(r2() * palette.length)]
            : (r2() < 0.5 ? palette[Math.floor(r2() * palette.length)] : 0x1f2430);
          mesh.setColorAt(i, c.setHex(col));
        } else {
          mesh.setColorAt(i, c.setHex(part === "clothes" ? 0x9aa0aa : 0x7a808a));
        }
      });
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      mesh.frustumCulled = false;
      mesh.userData.crowd = true;
      group.add(mesh);
    }
  };
  for (let pose = 0; pose < 3; pose++) place(spots[pose], personGeo(pose), false);
  place(seatedSpots, personGeo(0, true), true);
  group.userData.count = spots.reduce((a, l) => a + l.length, 0) + seatedSpots.length;
  return group;
}

function buildPerformers(v) {
  const group = new THREE.Group();
  const marks = (v.objects || []).filter((o) => o.kind === "mark");
  if (!marks.length) return group;
  const geo = personGeo(0);
  const mat = crowdMaterial(0xffffff);
  for (const [part, col] of [["clothes", 0x9ca3af], ["skin", 0xd6b48c]]) {
    const mesh = new THREE.InstancedMesh(geo[part], mat, marks.length);
    const m = new THREE.Matrix4(), c = new THREE.Color(col);
    marks.forEach((o, i) => {
      m.compose(new THREE.Vector3(o.x, o.y || 0, o.z), new THREE.Quaternion(), new THREE.Vector3(1.75, 1.75, 1.75));
      mesh.setMatrixAt(i, m);
      mesh.setColorAt(i, c);
    });
    mesh.userData.performer = true;
    group.add(mesh);
  }
  return group;
}

// ---------------------------------------------------------------------------
// zones: a tinted floor patch and an outline, shown while editing
// ---------------------------------------------------------------------------
export const ZONE_COLOURS = {
  dancefloor: 0xa855f7, standing: 0x3b82f6, seating: 0x22c55e, bar: 0xf59e0b,
  dj: 0xef4444, foh: 0x14b8a6, vip: 0xeab308, backstage: 0x64748b, stage: 0xf97316,
};

function buildZones(v) {
  const group = new THREE.Group();
  for (const z of v.zones || []) {
    const shape = new THREE.Shape(z.points.map(([x, zz]) => new THREE.Vector2(x, -zz)));
    const geo = new THREE.ShapeGeometry(shape);
    geo.rotateX(-Math.PI / 2);
    const col = ZONE_COLOURS[z.kind] ?? 0x94a3b8;
    const fill = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
      color: col, transparent: true, opacity: 0.13, depthWrite: false, toneMapped: false,
      polygonOffset: true, polygonOffsetFactor: -3,
    }));
    fill.position.y = (z.y || 0) + 0.012;
    fill.userData = { venueId: z.id, venueKind: z.kind, zone: true };
    const pts = z.points.map(([x, zz]) => new THREE.Vector3(x, (z.y || 0) + 0.015, zz));
    pts.push(pts[0].clone());
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.8, toneMapped: false }));
    group.add(fill, line);
  }
  return group;
}

// ---------------------------------------------------------------------------
// the venue
// ---------------------------------------------------------------------------
/**
 * Build everything.  Returns the scene group plus what the stage needs
 * each frame: the room box, the planes and boxes a beam stops on, the walls
 * (for the cutaway) and handles for the editor.
 */
export function buildVenue(venueIn, fixtures, opts = {}) {
  const v = effectiveVenue(venueIn, fixtures);
  const R = roomBox(v);
  const M = mats(v);
  const group = new THREE.Group();
  const outdoor = v.room.ceiling === "none";
  const edgeParts = [];

  // floor: the room, plus a wide apron outside it so the cutaway view has ground
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(R.w, R.d), M.floor);
  floor.rotation.x = -Math.PI / 2;
  floor.position.set(0, 0, R.z0 + R.d / 2);
  floor.userData = { venueKind: "floor" };
  group.add(floor);
  const apron = new THREE.Mesh(new THREE.PlaneGeometry(R.w + 60, R.d + 60),
    surfaceMaterial(outdoor ? 0x1d2618 : 0x0b0c0f));
  apron.rotation.x = -Math.PI / 2;
  apron.position.set(0, -0.01, R.z0 + R.d / 2);
  group.add(apron);

  // walls: one per side of the room, each with its outward normal
  const walls = [];
  if (!outdoor) {
    const sides = [
      { w: R.w, x: 0, z: R.z0, ry: 0, n: [0, 0, -1] },
      { w: R.w, x: 0, z: R.z1, ry: Math.PI, n: [0, 0, 1] },
      { w: R.d, x: R.x0, z: R.z0 + R.d / 2, ry: Math.PI / 2, n: [-1, 0, 0] },
      { w: R.d, x: R.x1, z: R.z0 + R.d / 2, ry: -Math.PI / 2, n: [1, 0, 0] },
    ];
    for (const s of sides) {
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(s.w, R.h), M.wall);
      mesh.position.set(s.x, R.h / 2, s.z);
      mesh.rotation.y = s.ry;
      mesh.userData = { venueKind: "wall" };
      group.add(mesh);
      walls.push({ mesh, normal: new THREE.Vector3(...s.n), centre: mesh.position.clone() });
    }
  }
  const box = [[R.x0, R.z0], [R.x1, R.z0], [R.x1, R.z1], [R.x0, R.z1]];
  for (let i = 0; i < 4; i++) {
    const [ax, az] = box[i], [bx, bz] = box[(i + 1) % 4];
    edgeParts.push(ax, 0.01, az, bx, 0.01, bz);
    if (!outdoor) {
      edgeParts.push(ax, R.h, az, bx, R.h, bz);
      edgeParts.push(ax, 0, az, ax, R.h, az);
    }
  }

  // ceiling: flat, or an open roof structure of beams
  let ceiling = null;
  if (v.room.ceiling === "flat") {
    ceiling = new THREE.Mesh(new THREE.PlaneGeometry(R.w, R.d), M.ceiling);
    ceiling.rotation.x = Math.PI / 2;
    ceiling.position.set(0, R.h, R.z0 + R.d / 2);
    group.add(ceiling);
  } else if (v.room.ceiling === "open") {
    const beams = [];
    for (let z = R.z0 + 2; z < R.z1; z += 4) {
      beams.push(boxGeo(R.w, 0.3, 0.15, 0, R.h + 0.15, z));
    }
    const roof = merged(beams, M.black);
    if (roof) group.add(roof);
  }

  // the stage deck and its skirt
  const s = v.stage;
  let stageFront = 0;
  if (s) {
    stageFront = s.z + s.depth;
    const hgt = Math.max(0.005, s.height);
    const top = new THREE.Mesh(new THREE.PlaneGeometry(s.width, s.depth), M.deck);
    top.rotation.x = -Math.PI / 2;
    top.position.set(s.x, hgt + 0.002, s.z + s.depth / 2);
    top.userData = { venueKind: "stage", venueId: "stage" };
    group.add(top);
    if (s.height > 0.01) {
      const skirt = new THREE.Mesh(new THREE.BoxGeometry(s.width, s.height, s.depth).translate(s.x, s.height / 2, s.z + s.depth / 2), M.skirt);
      skirt.userData = { venueKind: "stage", venueId: "stage" };
      group.add(skirt);
    }
    const x0 = s.x - s.width / 2, x1 = s.x + s.width / 2, z0 = s.z, z1 = s.z + s.depth;
    for (const [ax, az, bx, bz] of [[x0, z0, x1, z0], [x1, z0, x1, z1], [x1, z1, x0, z1], [x0, z1, x0, z0]]) {
      edgeParts.push(ax, hgt + 0.01, az, bx, hgt + 0.01, bz);
    }
  }

  const edges = new THREE.LineSegments(new THREE.BufferGeometry().setAttribute("position",
    new THREE.Float32BufferAttribute(edgeParts, 3)), M.edge);
  group.add(edges);

  // rigging and objects, one sub-group each so the editor can pick them
  const items = new Map();
  for (const r of v.rigging || []) {
    const g = buildRig(r, M, R);
    items.set(r.id, g);
    group.add(g);
  }
  for (const o of v.objects || []) {
    const g = buildObject(o, M);
    items.set(o.id, g);
    group.add(g);
  }

  const zones = buildZones(v);
  zones.visible = !!opts.zones;
  group.add(zones);

  const people = new THREE.Group();
  const crowd = buildCrowd(v, stageFront);
  people.add(crowd, buildPerformers(v));
  people.visible = opts.people !== false;
  group.add(people);

  const planes = [{ n: new THREE.Vector3(0, 1, 0), c: 0 }];
  if (!outdoor && v.room.ceiling === "flat") planes.push({ n: new THREE.Vector3(0, -1, 0), c: -R.h });
  if (!outdoor) {
    planes.push({ n: new THREE.Vector3(0, 0, 1), c: R.z0 }, { n: new THREE.Vector3(0, 0, -1), c: -R.z1 },
      { n: new THREE.Vector3(1, 0, 0), c: R.x0 }, { n: new THREE.Vector3(-1, 0, 0), c: -R.x1 });
  }

  return { group, venue: v, room: R, planes, boxes: blockers(v), walls, ceiling, zones, people,
    crowdCount: crowd.userData.count || 0, items, stageFront };
}

/** Hide the walls between the camera and the room, and the ceiling from above. */
export function cutaway(built, cameraPos) {
  if (!built) return;
  for (const w of built.walls) {
    const out = w.normal.dot(new THREE.Vector3().subVectors(cameraPos, w.centre));
    w.mesh.visible = out < 0.3;
  }
  if (built.ceiling) built.ceiling.visible = cameraPos.y < built.room.h - 0.05;
}

const _box = new THREE.Vector3();

/** Distance along a ray to the first surface or solid it meets (capped). */
export function hitDistance(planes, boxes, origin, dir, cap = 30) {
  let best = cap;
  for (const p of planes) {
    const denom = p.n.dot(dir);
    if (denom >= -1e-4) continue;
    const t = (p.c - p.n.dot(origin)) / denom;
    if (t > 0.02 && t < best) best = t;
  }
  const ray = new THREE.Ray(origin, dir);
  for (const b of boxes || []) {
    if (b.containsPoint(origin)) continue;
    const hit = ray.intersectBox(b, _box);
    if (hit) {
      const t = hit.distanceTo(origin);
      if (t > 0.02 && t < best) best = t;
    }
  }
  return best;
}
