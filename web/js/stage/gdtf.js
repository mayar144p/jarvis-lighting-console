// Real fixture geometry from a GDTF file: the manufacturer's own meshes,
// hierarchy, pivots and beam, driven by the same pan/tilt as every other
// head.  Used for any head whose fixture file carries 3D models; the
// procedural model (models.js) is what the stage shows while this loads
// and whenever it cannot.
//
// GDTF is +Z up with the beam along -Z, so a definition at identity is a
// HUNG fixture.  The whole tree sits under one converter group that turns
// +Z up into three.js's +Y up; inside it every node keeps its GDTF frame,
// which is why pan is a rotation about local Z and tilt about local X.
import * as THREE from "three";
import { parse3DS } from "./parse3ds.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";

const parsed = new Map();          // defId + model name -> Promise<Object3D|null>

function snapScale(k) {
  if (!isFinite(k) || k <= 0) return 1;
  const p = Math.pow(10, Math.round(Math.log10(k)));
  return Math.abs(k / p - 1) < 0.02 ? p : k;
}

async function parseModel(bytes, ext) {
  switch (ext) {
    case ".3ds":
      return parse3DS(bytes);
    case ".glb":
    case ".gltf": {
      const gltf = await new GLTFLoader().parseAsync(bytes, "");
      const wrap = new THREE.Group();
      gltf.scene.rotation.x = Math.PI / 2;           // glTF +Y up -> GDTF +Z up
      wrap.add(gltf.scene);
      return wrap;
    }
    case ".stl":
      return new THREE.Mesh(new STLLoader().parse(bytes));
    case ".obj":
      return new OBJLoader().parse(new TextDecoder().decode(bytes));
    default:
      return null;
  }
}

function loadModel(def, name, fetchBytes) {
  const key = def.id + "\u0000" + name;
  if (!parsed.has(key)) {
    const entry = (def.models || {})[name];
    const job = entry
      ? fetchBytes(def.id, entry).then((buf) => parseModel(buf, entry.ext)).catch(() => null)
      : Promise.resolve(null);
    parsed.set(key, job);
  }
  return parsed.get(key).then((obj) => (obj ? obj.clone(true) : null));
}

function fit(obj, meta) {
  if (!obj || !meta) return;
  const box = new THREE.Box3().setFromObject(obj);
  const size = box.getSize(new THREE.Vector3());
  const got = Math.max(size.x, size.y, size.z);
  const want = Math.max(+meta.width || 0, +meta.height || 0, +meta.length || 0);
  if (got > 0 && want > 0) obj.scale.multiplyScalar(snapScale(want / got));
}

function primitive(meta) {
  if (!meta) return null;
  const L = +meta.length || 0.1, W = +meta.width || 0.1, H = +meta.height || 0.1;
  const kind = String(meta.primitive || "").toLowerCase();
  if (kind.includes("cylinder") || kind.includes("head") || kind.includes("conventional")) {
    const g = new THREE.CylinderGeometry(Math.max(L, W) / 2, Math.max(L, W) / 2, H, 28);
    g.rotateX(Math.PI / 2);                     // cylinder axis along GDTF Z
    return new THREE.Mesh(g);
  }
  if (!kind) return null;
  return new THREE.Mesh(new THREE.BoxGeometry(L, W, H));
}

function paint(obj, material) {
  obj.traverse((o) => {
    if (o.isMesh) {
      o.material = material;
      if (o.geometry && !o.geometry.attributes.normal) o.geometry.computeVertexNormals();
    }
  });
}

/**
 * Build a skeleton (same contract as models.js) from a GDTF definition.
 * Resolves to null when the definition has no geometry worth drawing.
 */
export async function buildGdtf(def, body, fetchBytes, housingMat, lensMat) {
  if (!def || !def.ok || !Array.isArray(def.nodes) || !def.nodes.length) return null;
  const root = new THREE.Group();
  const convert = new THREE.Group();
  convert.rotation.x = -Math.PI / 2;               // GDTF +Z up -> +Y up
  root.add(convert);
  const kin = def.kinematics || {};
  const sk = {
    root, emitters: [], lenses: [lensMat], native: "hung", gdtf: true,
    panPivot: null, tiltPivot: null, height: 0.5, radius: 0.35,
  };
  const jobs = [];
  let drew = 0;

  const walk = (node, parent, path) => {
    const obj = new THREE.Group();
    const m = new THREE.Matrix4();
    if (Array.isArray(node.matrix) && node.matrix.length === 16) m.set(...node.matrix);
    m.decompose(obj.position, obj.quaternion, obj.scale);
    parent.add(obj);
    const pivot = new THREE.Group();
    obj.add(pivot);
    if (path === kin.pan) sk.panPivot = pivot;
    if (path === kin.tilt) sk.tiltPivot = pivot;
    if (node.kind === "beam") {
      const e = new THREE.Object3D();
      pivot.add(e);
      sk.emitters.push({
        node: e, radius: Math.max(0.01, +node.beam_radius || 0.05),
        dir: new THREE.Vector3(0, 0, -1),
        beamAngle: +node.beam_angle || 0, fieldAngle: +node.field_angle || 0,
      });
      const lens = new THREE.Mesh(new THREE.CircleGeometry(Math.max(0.01, +node.beam_radius || 0.05), 28), lensMat);
      lens.rotation.x = Math.PI;                   // faces -Z, out of the lens
      lens.position.z = -0.001;
      pivot.add(lens);
    }
    if (node.model && node.kind !== "beam") {
      const meta = (def.modelMeta || {})[node.model];
      jobs.push(loadModel(def, node.model, fetchBytes).then((mesh) => {
        const part = mesh || primitive(meta);
        if (!part) return;
        if (mesh) fit(part, meta);
        paint(part, housingMat);
        pivot.add(part);
        drew++;
      }));
    }
    (node.children || []).forEach((c, i) => walk(c, pivot, path + "/" + i));
  };
  def.nodes.forEach((n, i) => walk(n, convert, String(i)));
  await Promise.all(jobs);
  if (!drew) return null;

  sk.setPan = (rad) => { if (sk.panPivot) sk.panPivot.rotation.z = rad; };
  sk.setTilt = (rad) => { if (sk.tiltPivot) sk.tiltPivot.rotation.x = rad; };
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3());
  sk.height = Math.max(0.2, size.y);
  sk.radius = Math.max(0.15, Math.max(size.x, size.z) / 2 + 0.05);
  if (!sk.emitters.length) {
    const e = new THREE.Object3D();
    (sk.tiltPivot || sk.panPivot || convert).add(e);
    sk.emitters.push({ node: e, radius: 0.05, dir: new THREE.Vector3(0, 0, -1) });
  }
  return sk;
}
