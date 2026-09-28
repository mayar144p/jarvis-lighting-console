// The Jarvis stage: a live 3D view of the rig.
//
// It never computes DMX.  The engine decides what every head is doing and
// sends it on the light feed ({hex, a, pan, tilt, deg, beam}); this module
// turns that into moving yokes, glowing lenses, volumetric beams and pools
// of light on every surface.  Heads appear the moment they are patched,
// modelled for their physical type and styled for their brand, and swap to
// the manufacturer's own GDTF meshes when those load.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { buildFixture } from "./models.js";
import { buildGdtf } from "./gdtf.js";
import { buildVenue, hitDistance, roomFor } from "./venue.js";
import {
  LIGHTS, MAX_LIGHTS, beamGeometry, beamMaterial, glowMap,
} from "./materials.js";

const DEG = Math.PI / 180;
const UP = new THREE.Vector3(0, 1, 0);
const DEFAULT_PAN = [-270, 270];
const DEFAULT_TILT = [-135, 135];

function smooth(t) { return t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t); }
function lerp(a, b, t) { return a + (b - a) * t; }

function hexLinear(hex, out) {
  out.set(hex || "#ffffff");            // Color.set converts sRGB -> linear
  return out;
}

const EMPTY_LOOK = { a: 0, r: 1, g: 1, b: 1, pan: null, tilt: null, beam: {} };

function lookFrom(row, scratch) {
  if (!row) return { ...EMPTY_LOOK, beam: {} };
  const c = hexLinear(row.hex, scratch);
  return {
    a: +row.a || 0, r: c.r, g: c.g, b: c.b,
    pan: typeof row.pan === "number" ? row.pan : null,
    tilt: typeof row.tilt === "number" ? row.tilt : null,
    deg: row.deg || null,
    beam: row.beam || {},
  };
}

function mixLook(a, b, t) {
  const beam = {};
  for (const k of new Set([...Object.keys(a.beam), ...Object.keys(b.beam)])) {
    const x = a.beam[k], y = b.beam[k];
    beam[k] = x === undefined ? y : y === undefined ? x : lerp(x, y, t);
  }
  const ang = (x, y) => (x === null ? y : y === null ? x : lerp(x, y, t));
  return {
    a: lerp(a.a, b.a, t), r: lerp(a.r, b.r, t), g: lerp(a.g, b.g, t),
    b: lerp(a.b, b.b, t), pan: ang(a.pan, b.pan), tilt: ang(a.tilt, b.tilt),
    deg: b.deg || a.deg, beam,
  };
}

export class Stage {
  constructor(container, opts = {}) {
    this.el = container;
    this.opts = opts;
    this.fixtures = new Map();            // head_no -> instance
    this.selected = new Set();
    this.options = { haze: 0.6, bloom: true, people: true, labels: true };
    this.t0 = performance.now();
    this.dirty = true;
    this.venueSig = "";
    this.venueData = null;
    this.room = { w: 10, d: 8, h: 7 };
    this.planes = [];
    this._c = new THREE.Color();
    this._v1 = new THREE.Vector3();
    this._v2 = new THREE.Vector3();
    this._q = new THREE.Quaternion();
    this.gdtfDefs = new Map();
    this.fetchModel = null;
    this.destroyed = false;

    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    } catch (e) {
      this.failed = true;
      container.innerHTML = '<div class="stage-fallback">3D view needs WebGL, which this browser has turned off.</div>';
      return;
    }
    this.renderer = renderer;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.domElement.className = "stage-canvas";
    renderer.domElement.tabIndex = 0;
    container.appendChild(renderer.domElement);

    this.labels = document.createElement("div");
    this.labels.className = "stage-labels";
    container.appendChild(this.labels);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x040508);
    this.scene = scene;
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.add(new THREE.HemisphereLight(0x8fa3c7, 0x0b0b10, 0.22));
    const key = new THREE.DirectionalLight(0xdfe7ff, 0.35);
    key.position.set(4, 9, 12);
    scene.add(key);

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.05, 400);
    this.camera.position.set(0, 4, 18);
    this.controls = new OrbitControls(this.camera, renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.09;
    this.controls.screenSpacePanning = true;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.controls.minDistance = 0.6;
    this.controls.maxDistance = 120;
    this.controls.addEventListener("change", () => {
      this.dirty = true;
      if (this.opts.onCamera) this.opts.onCamera(this.cameraState());
    });

    this.composer = new EffectComposer(renderer);
    this.composer.addPass(new RenderPass(scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.55, 0.4, 0.9);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    this.venueGroup = new THREE.Group();
    scene.add(this.venueGroup);
    this.rigGroup = new THREE.Group();
    scene.add(this.rigGroup);
    this.beamGroup = new THREE.Group();
    scene.add(this.beamGroup);

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this._wirePointer();
    this._wireKeys();

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();
    this.buildVenue(null, []);
    this.view("front", true);
    this._loop = this._loop.bind(this);
    this._raf = requestAnimationFrame(this._loop);
  }

  // ---------------------------------------------------------------- sizing
  resize() {
    if (!this.renderer) return;
    const w = Math.max(1, this.el.clientWidth);
    const h = Math.max(1, this.el.clientHeight);
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.bloom.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  // ----------------------------------------------------------------- venue
  buildVenue(venue, fixtures) {
    const room = roomFor(venue || {}, fixtures);
    const sig = JSON.stringify([room, (venue && venue.surfaces) || [],
      fixtures.filter((f) => f.kind === "truss" || f.y >= 2)
        .map((f) => [Math.round(f.x * 2), Math.round(f.y * 2), Math.round(f.z * 2)])]);
    if (sig === this.venueSig) return;
    this.venueSig = sig;
    this.venueGroup.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
    this.venueGroup.clear();
    const built = buildVenue(venue || {}, fixtures, { people: this.options.people });
    this.venueGroup.add(built.group);
    this.room = built.room;
    this.planes = built.planes;
    this.people = built.people;
    this.dirty = true;
  }

  // ------------------------------------------------------------------- rig
  /**
   * Replace the rig.  Heads keep their instance (and their look) when only
   * their position changed; a head whose TYPE changed is rebuilt; new heads
   * drop in with a short animation so a newly patched light is obvious.
   */
  setRig({ fixtures = [], venue = null } = {}) {
    if (this.failed) return;
    this.venueData = venue;
    const seen = new Set();
    for (const f of fixtures) {
      seen.add(f.head_no);
      const sig = [f.body && f.body.type, f.body && f.body.brand, f.model, f.mode, (f.body && f.body.cells) || 1].join("|");
      let inst = this.fixtures.get(f.head_no);
      if (inst && inst.sig !== sig) {
        this._remove(inst);
        inst = null;
      }
      if (!inst) {
        inst = this._create(f, sig);
        this.fixtures.set(f.head_no, inst);
      }
      inst.data = f;
      this._place(inst);
    }
    for (const [head, inst] of this.fixtures) {
      if (!seen.has(head)) {
        this._remove(inst);
        this.fixtures.delete(head);
      }
    }
    this.buildVenue(venue, fixtures);
    for (const inst of this.fixtures.values()) this._aimStatic(inst);
    this._applyGdtf();
    this.dirty = true;
  }

  _create(f, sig) {
    const sk = buildFixture(f.body, `${f.manufacturer || ""} ${f.model || ""}`);
    const inst = {
      head: f.head_no, sig, data: f, sk,
      holder: new THREE.Group(),
      beams: [],
      from: { ...EMPTY_LOOK, beam: {} }, to: { ...EMPTY_LOOK, beam: {} },
      cur: { ...EMPTY_LOOK, beam: {} }, t0: 0, dur: 0,
      born: performance.now(),
      staticAim: null,
    };
    inst.holder.add(sk.root);
    inst.holder.userData.head = f.head_no;
    // an invisible, generous hit volume so a light is easy to click
    const pick = new THREE.Mesh(new THREE.CylinderGeometry(sk.radius, sk.radius, sk.height, 12),
      new THREE.MeshBasicMaterial({ visible: false }));
    pick.userData.head = f.head_no;
    inst.pick = pick;
    inst.holder.add(pick);
    this.rigGroup.add(inst.holder);
    this._buildBeams(inst);
    return inst;
  }

  _buildBeams(inst) {
    for (const b of inst.beams) {
      this.beamGroup.remove(b.mesh);
      this.beamGroup.remove(b.glow);
      b.mat.dispose();
      b.glow.material.dispose();
    }
    inst.beams = inst.sk.emitters.map((em) => {
      const mat = beamMaterial();
      const mesh = new THREE.Mesh(beamGeometry(), mat);
      mesh.frustumCulled = false;
      mesh.visible = false;
      mesh.renderOrder = 2;
      this.beamGroup.add(mesh);
      const glow = new THREE.Sprite(new THREE.SpriteMaterial({
        map: glowMap(), blending: THREE.AdditiveBlending, depthWrite: false,
        transparent: true, toneMapped: false,
      }));
      glow.visible = false;
      glow.renderOrder = 3;
      this.beamGroup.add(glow);
      return { em, mesh, mat, glow, origin: new THREE.Vector3(), dir: new THREE.Vector3() };
    });
  }

  _remove(inst) {
    this.rigGroup.remove(inst.holder);
    for (const b of inst.beams) {
      this.beamGroup.remove(b.mesh);
      this.beamGroup.remove(b.glow);
      b.mat.dispose();
      b.glow.material.dispose();
    }
  }

  /** Hang or stand the model, the right way up, where the patch says. */
  _place(inst) {
    const f = inst.data;
    const hung = (f.kind || (f.y >= 2 ? "truss" : "floor")) === "truss";
    inst.hung = hung;
    inst.holder.position.set(+f.x || 0, +f.y || 0, +f.z || 0);
    const flip = inst.sk.native === "hung" ? !hung : hung;
    inst.sk.root.rotation.set(0, 0, flip ? Math.PI : 0);
    inst.pick.position.y = (hung ? -1 : 1) * inst.sk.height / 2;
    inst.staticAim = null;
  }

  /** Where a light with no pan/tilt points: a sensible stage position. */
  _aimStatic(inst) {
    const body = inst.data.body || {};
    const moving = body.moving;
    if (moving && !inst.sk.gdtf) return;
    const { w, d, h } = this.room;
    const f = inst.data;
    const p = new THREE.Vector3(+f.x || 0, +f.y || 0, +f.z || 0);
    let target;
    if (body.type === "tube") {
      target = new THREE.Vector3(p.x * 1.2, p.y, p.z + 6);
    } else if (body.type === "followspot") {
      target = new THREE.Vector3(0, 1.4, d * 0.5);
    } else if (!inst.hung) {
      target = (p.z < 1.2 || body.type === "cyc")
        ? new THREE.Vector3(p.x, h * 0.72, -0.6)
        : new THREE.Vector3(p.x * 0.9, h, p.z + 0.4);
    } else if (p.z > d * 0.75) {
      target = new THREE.Vector3(p.x * 0.6, 1.3, d * 0.45);
    } else {
      target = new THREE.Vector3(p.x * 0.9, 0, Math.min(d - 0.4, p.z + 1.4));
    }
    if (moving) return;               // GDTF movers: driven by the feed
    const dir = target.sub(p).normalize();
    this._setStaticDir(inst, dir);
    void w;
  }

  _setStaticDir(inst, worldDir) {
    const sk = inst.sk;
    if (sk.gdtf) return;
    const local = worldDir.clone().applyQuaternion(
      sk.root.getWorldQuaternion(this._q).invert());
    const yaw = Math.atan2(local.x, local.z);
    const tilt = Math.atan2(Math.hypot(local.x, local.z), local.y);
    sk.pan.rotation.y = yaw;
    sk.tilt.rotation.x = tilt;
    inst.staticAim = { yaw, tilt };
  }

  setPositions(moves) {
    for (const [head, pos] of Object.entries(moves || {})) {
      const inst = this.fixtures.get(Number(head));
      if (!inst) continue;
      Object.assign(inst.data, pos);
      this._place(inst);
      this._aimStatic(inst);
    }
    this.dirty = true;
  }

  // ------------------------------------------------------------------ GDTF
  /** Swap in the manufacturer's meshes for heads whose file has them. */
  setGdtf(definitions, fetchModel) {
    this.fetchModel = fetchModel;
    for (const def of definitions || []) {
      if (def && def.ok) this.gdtfDefs.set(def.id, def);
    }
    this._applyGdtf();
  }

  _applyGdtf() {
    if (!this.fetchModel) return;
    for (const def of this.gdtfDefs.values()) {
      for (const head of def.heads || []) {
        const inst = this.fixtures.get(head);
        if (!inst || inst.gdtfId === def.id || inst.gdtfPending) continue;
        inst.gdtfPending = true;
        const sk0 = inst.sk;
        const lensMat = sk0.lenses[0] || new THREE.MeshStandardMaterial();
        let housingMat = null;
        sk0.root.traverse((o) => {
          if (!housingMat && o.isMesh && o.material && o.material.isMeshStandardMaterial
              && o.material !== lensMat) housingMat = o.material;
        });
        buildGdtf(def, inst.data.body, this.fetchModel,
          housingMat || new THREE.MeshStandardMaterial({ color: 0x1b1c20 }), lensMat)
          .then((sk) => {
            inst.gdtfPending = false;
            if (!sk || this.destroyed || this.fixtures.get(head) !== inst) return;
            inst.holder.remove(sk0.root);
            inst.sk = sk;
            inst.gdtfId = def.id;
            inst.holder.add(sk.root);
            this._place(inst);
            this._buildBeams(inst);
            this.dirty = true;
          })
          .catch(() => { inst.gdtfPending = false; });
      }
    }
  }

  // ----------------------------------------------------------------- looks
  /** The light feed: {head: {hex, a, pan?, tilt?, deg?, beam?}}. */
  setLooks(looks, holdMs = 120) {
    const now = performance.now();
    for (const [head, inst] of this.fixtures) {
      const row = looks ? looks[head] : null;
      inst.from = inst.cur;
      inst.to = lookFrom(row, this._c);
      inst.t0 = now;
      inst.dur = Math.max(0, holdMs);
    }
    this.dirty = true;
    return true;
  }

  setSelected(list) {
    this.selected = new Set((list || []).map(Number));
    this.dirty = true;
  }

  setOptions(o) {
    Object.assign(this.options, o || {});
    if (this.people) this.people.visible = !!this.options.people;
    this.dirty = true;
  }

  // ---------------------------------------------------------------- camera
  cameraState() {
    return { pos: this.camera.position.toArray(), target: this.controls.target.toArray() };
  }

  setCamera(s) {
    if (!s || !s.pos || !s.target) return;
    this.camera.position.fromArray(s.pos);
    this.controls.target.fromArray(s.target);
    this.controls.update();
    this.dirty = true;
  }

  view(name, instant = false) {
    const { w, d } = this.room;
    const t = new THREE.Vector3(0, 1.6, d * 0.45);
    const at = {
      front: [0, 3.4, d + 10.5],
      left: [-(w / 2 + 9), 4.2, d * 0.5],
      right: [w / 2 + 9, 4.2, d * 0.5],
      back: [0, 5.5, -9],
      top: [0, 24, d * 0.45 + 0.01],
      house: [0, 6.5, d + 16],
    }[name] || [0, 3.4, d + 10.5];
    this._flyTo(new THREE.Vector3(...at), t, instant);
  }

  _flyTo(pos, target, instant) {
    if (instant) {
      this.camera.position.copy(pos);
      this.controls.target.copy(target);
      this.controls.update();
      this.dirty = true;
      return;
    }
    this.fly = {
      p0: this.camera.position.clone(), p1: pos,
      t0: this.controls.target.clone(), t1: target,
      start: performance.now(), dur: 650,
    };
  }

  frame(heads) {
    const pts = [];
    for (const inst of this.fixtures.values()) {
      if (heads && heads.length && !heads.includes(inst.head)) continue;
      pts.push(inst.holder.position.clone());
    }
    if (!pts.length) return this.view("front");
    const box = new THREE.Box3().setFromPoints(pts);
    box.expandByScalar(1.2);
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    const dist = sphere.radius / Math.sin((this.camera.fov * DEG) / 2) * 1.1;
    this._flyTo(sphere.center.clone().add(dir.multiplyScalar(Math.max(dist, 3))), sphere.center, false);
  }

  zoom(factor) {
    const dir = this.camera.position.clone().sub(this.controls.target);
    dir.multiplyScalar(1 / Math.max(0.2, factor));
    this._flyTo(this.controls.target.clone().add(dir), this.controls.target.clone(), false);
  }

  // --------------------------------------------------------- pick and drag
  _hit(ev) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const picks = [...this.fixtures.values()].map((i) => i.pick);
    const hit = this.raycaster.intersectObjects(picks, false)[0];
    return hit ? this.fixtures.get(hit.object.userData.head) : null;
  }

  _wirePointer() {
    const dom = this.renderer.domElement;
    let down = null;
    dom.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      const inst = this._hit(ev);
      down = inst ? { inst, x: ev.clientX, y: ev.clientY, dragging: false, ev } : null;
    });
    dom.addEventListener("pointermove", (ev) => {
      if (!down) {
        const inst = this._hit(ev);
        const h = inst ? inst.head : null;
        if (h !== this.hover) { this.hover = h; this.dirty = true; }
        dom.style.cursor = inst ? (this.opts.editable === false ? "pointer" : "grab") : "";
        return;
      }
      if (this.opts.editable === false) return;
      if (!down.dragging && Math.hypot(ev.clientX - down.x, ev.clientY - down.y) > 5) {
        down.dragging = true;
        this.controls.enabled = false;
        dom.setPointerCapture(ev.pointerId);
        const p = down.inst.holder.position;
        down.plane = ev.shiftKey
          ? new THREE.Plane().setFromNormalAndCoplanarPoint(
            this.camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize().negate(), p)
          : new THREE.Plane(new THREE.Vector3(0, 1, 0), -p.y);
        down.vertical = ev.shiftKey;
        dom.style.cursor = "grabbing";
      }
      if (down.dragging) {
        const r = dom.getBoundingClientRect();
        this.pointer.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
        this.raycaster.setFromCamera(this.pointer, this.camera);
        const at = this.raycaster.ray.intersectPlane(down.plane, new THREE.Vector3());
        if (!at) return;
        const f = down.inst.data;
        const snap = (v) => Math.round(v * 20) / 20;
        if (down.vertical) f.y = Math.max(0, Math.min(this.room.h + 3, snap(at.y)));
        else { f.x = snap(at.x); f.z = snap(at.z); }
        f.kind = f.y >= 2 ? "truss" : "floor";
        this._place(down.inst);
        this._aimStatic(down.inst);
        this.dirty = true;
        const now = performance.now();
        if (this.opts.onMoveFixture && (!down.sent || now - down.sent > 120)) {
          down.sent = now;
          this.opts.onMoveFixture(f.head_no, f.x, f.y, f.z, false);
        }
      }
    });
    const end = (ev) => {
      if (!down) return;
      const d0 = down;
      down = null;
      this.controls.enabled = true;
      dom.style.cursor = "";
      if (d0.dragging) {
        const f = d0.inst.data;
        this.buildVenue(this.venueData, [...this.fixtures.values()].map((i) => i.data));
        if (this.opts.onMoveFixture) this.opts.onMoveFixture(f.head_no, f.x, f.y, f.z, true);
      } else if (this.opts.onPick) {
        this.opts.onPick(d0.inst.head, { shift: ev.shiftKey, toggle: ev.ctrlKey || ev.metaKey });
      }
    };
    dom.addEventListener("pointerup", end);
    dom.addEventListener("pointercancel", end);
    dom.addEventListener("dblclick", (ev) => {
      const inst = this._hit(ev);
      if (inst) this.frame([inst.head]);
    });
  }

  _wireKeys() {
    const dom = this.renderer.domElement;
    const views = { 1: "front", 2: "left", 3: "right", 4: "back", 5: "top" };
    dom.addEventListener("keydown", (ev) => {
      if (views[ev.key]) { this.view(views[ev.key]); ev.preventDefault(); ev.stopPropagation(); }
      else if (ev.key === "0") { this.view("front"); ev.preventDefault(); ev.stopPropagation(); }
      else if (ev.key === "f" || ev.key === "F") {
        this.frame(this.selected.size ? [...this.selected] : null);
        ev.preventDefault(); ev.stopPropagation();
      }
    });
  }

  // ------------------------------------------------------------ the frame
  _loop() {
    if (this.destroyed) return;
    this._raf = requestAnimationFrame(this._loop);
    if (document.hidden) return;
    const now = performance.now();
    const time = (now - this.t0) / 1000;
    LIGHTS.uTime.value = time;
    let animating = false;

    if (this.fly) {
      const t = smooth((now - this.fly.start) / this.fly.dur);
      this.camera.position.lerpVectors(this.fly.p0, this.fly.p1, t);
      this.controls.target.lerpVectors(this.fly.t0, this.fly.t1, t);
      if (t >= 1) this.fly = null;
      animating = true;
    }
    if (this.controls.update()) animating = true;

    let lit = 0;
    const lights = [];
    for (const inst of this.fixtures.values()) {
      // entrance: a light that was just patched drops into place
      const age = (now - inst.born) / 420;
      if (age < 1) {
        const k = 1 - Math.pow(1 - age, 3);
        inst.sk.root.scale.setScalar(0.2 + 0.8 * k);
        inst.sk.root.position.y = (inst.hung ? -1 : 1) * 0.5 * (1 - k);
        animating = true;
      } else if (inst.sk.root.scale.x !== 1) {
        inst.sk.root.scale.setScalar(1);
        inst.sk.root.position.y = 0;
      }
      const t = inst.dur > 0 ? Math.min(1, (now - inst.t0) / inst.dur) : 1;
      if (t < 1) animating = true;
      inst.cur = mixLook(inst.from, inst.to, smooth(t));
      if (this._drive(inst, time, lights)) lit++;
    }
    this.rigGroup.updateMatrixWorld(true);
    for (const inst of this.fixtures.values()) this._updateBeams(inst, time, lights);
    this._uploadLights(lights);
    if (lit) animating = true;

    if (this.dirty || animating) {
      this.dirty = false;
      this.bloom.enabled = !!this.options.bloom;
      this.composer.render();
      this._drawLabels();
    }
  }

  /** Apply one head's look to its model.  Returns true when it emits. */
  _drive(inst, time, lights) {
    const L = inst.cur;
    const sk = inst.sk;
    const moving = inst.data.body && inst.data.body.moving;
    if (moving || sk.gdtf) {
      const deg = L.deg || {};
      const pr = deg.pan || DEFAULT_PAN;
      const tr = deg.tilt || DEFAULT_TILT;
      const pan = L.pan === null ? 0 : lerp(pr[0], pr[1], L.pan) * DEG;
      const tilt = L.tilt === null ? 0 : lerp(tr[0], tr[1], L.tilt) * DEG;
      if (sk.gdtf) {
        if (moving) { sk.setPan(pan); sk.setTilt(tilt); }
      } else {
        sk.pan.rotation.y = pan;
        sk.tilt.rotation.x = tilt;
      }
    }
    let a = L.a;
    const strobe = L.beam.strobe || 0;
    if (strobe > 0.05) {
      const hz = 1 + strobe * 19;
      if ((time * hz) % 1 > 0.28) a = 0;
    }
    inst.level = a;
    const col = this._c.setRGB(L.r, L.g, L.b);
    for (const lens of sk.lenses) {
      lens.emissive.copy(col).multiplyScalar(a * 7);
    }
    void lights;
    return a > 0.002;
  }

  _updateBeams(inst, time, lights) {
    const L = inst.cur;
    const a = inst.level || 0;
    const body = inst.data.body || {};
    const bmin = (body.beam && body.beam.min) || 20;
    const bmax = (body.beam && body.beam.max) || bmin;
    const zoom = L.beam.zoom;
    const iris = L.beam.iris || 0;
    const frost = L.beam.frost || 0;
    const goboV = L.beam.gobo || 0;
    for (const b of inst.beams) {
      const em = b.em;
      let angle = em.fieldAngle || (zoom === undefined
        ? (bmax > bmin ? (bmin + bmax) / 2 : bmin) : lerp(bmin, bmax, zoom));
      angle *= 1 - iris * 0.6;
      if (em.laser) angle = 0.25;
      if (body.type === "atmos" || angle <= 0 || a <= 0.002) {
        b.mesh.visible = false;
        b.glow.visible = false;
        continue;
      }
      em.node.getWorldPosition(b.origin);
      b.dir.copy(em.dir).transformDirection(em.node.matrixWorld).normalize();
      const len = hitDistance(this.planes, b.origin, b.dir, 26);
      const half = (angle / 2) * DEG;
      const r0 = Math.max(0.01, em.radius);
      const r1 = r0 + Math.tan(half) * len;
      b.mesh.position.copy(b.origin);
      b.mesh.quaternion.setFromUnitVectors(UP, b.dir);
      const u = b.mat.uniforms;
      u.uLen.value = len;
      u.uR0.value = r0;
      u.uR1.value = r1;
      u.uColor.value.setRGB(L.r, L.g, L.b);
      const narrow = Math.min(3.2, Math.max(0.45, Math.sqrt(22 / Math.max(angle, 1))));
      u.uIntensity.value = a * 0.55 * narrow / Math.max(1, inst.beams.length * 0.6);
      u.uSoft.value = lerp(1.7, 0.55, frost);
      u.uHaze.value = this.options.haze;
      b.mesh.visible = this.options.haze > 0.01;

      // head-on lens glow
      const toCam = this._v1.copy(this.camera.position).sub(b.origin).normalize();
      const facing = Math.max(0, toCam.dot(b.dir));
      b.glow.visible = true;
      b.glow.position.copy(b.origin).addScaledVector(b.dir, 0.01);
      const g = r0 * (3 + 12 * Math.pow(facing, 6)) * (0.4 + a);
      b.glow.scale.setScalar(g);
      b.glow.material.color.setRGB(L.r, L.g, L.b).multiplyScalar(0.35 + 1.8 * Math.pow(facing, 4) * a);

      const goboId = goboV > 0.06 ? 1 + (Math.floor(goboV * 7.99) % 7) : 0;
      const rot = (L.beam.gobo_rot || 0) > 0.03 ? time * (L.beam.gobo_rot - 0.03) * 6 : 0;
      const power = a * 9 * Math.min(4, Math.pow(26 / Math.max(angle, 2), 1.1)) / inst.beams.length;
      lights.push({
        pos: b.origin, dir: b.dir, r: L.r * power, g: L.g * power, b: L.b * power,
        cosO: Math.cos(half), cosI: Math.cos(half * lerp(0.72, 0.25, frost)),
        gobo: goboId, rot, weight: power * (L.r + L.g + L.b),
      });
    }
  }

  _uploadLights(lights) {
    lights.sort((x, y) => y.weight - x.weight);
    const n = Math.min(MAX_LIGHTS, lights.length);
    for (let i = 0; i < n; i++) {
      const l = lights[i];
      LIGHTS.uPos.value[i].copy(l.pos);
      LIGHTS.uDir.value[i].copy(l.dir);
      LIGHTS.uCol.value[i].set(l.r, l.g, l.b);
      LIGHTS.uCone.value[i].set(l.cosO, l.cosI, l.gobo, l.rot);
    }
    LIGHTS.uCount.value = n;
  }

  _drawLabels() {
    if (!this.options.labels) { this.labels.replaceChildren(); return; }
    const want = new Set(this.selected);
    if (this.hover !== null && this.hover !== undefined) want.add(this.hover);
    const w = this.el.clientWidth, h = this.el.clientHeight;
    const nodes = [];
    for (const head of want) {
      const inst = this.fixtures.get(head);
      if (!inst) continue;
      const p = inst.holder.position.clone();
      p.y += (inst.hung ? 0.25 : inst.sk.height + 0.12);
      p.project(this.camera);
      if (p.z > 1) continue;
      const tag = document.createElement("div");
      tag.className = "stage-tag" + (this.selected.has(head) ? " sel" : "");
      const d = inst.data;
      const brand = d.body && d.body.brand !== "generic" ? d.body.brand_name + " " : "";
      tag.textContent = `#${head}  ${brand}${d.model || ""}`.trim();
      tag.style.left = ((p.x + 1) / 2 * w) + "px";
      tag.style.top = ((1 - p.y) / 2 * h) + "px";
      nodes.push(tag);
    }
    this.labels.replaceChildren(...nodes);
    for (const inst of this.fixtures.values()) {
      const on = this.selected.has(inst.head);
      if (on && !inst.ring) {
        inst.ring = new THREE.Mesh(new THREE.RingGeometry(inst.sk.radius * 1.05, inst.sk.radius * 1.2, 40),
          new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.9, side: THREE.DoubleSide, toneMapped: false }));
        inst.ring.rotation.x = -Math.PI / 2;
        inst.holder.add(inst.ring);
      }
      if (inst.ring) {
        inst.ring.visible = on;
        inst.ring.position.y = inst.hung ? 0.02 : 0.01;
      }
    }
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this._raf);
    if (this.resizeObserver) this.resizeObserver.disconnect();
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
    }
    if (this.labels) this.labels.remove();
  }
}

/** A single fixture on a turntable, for the add-fixture dialog. */
export class FixturePreview {
  constructor(container) {
    this.el = container;
    try {
      this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch (e) {
      this.failed = true;
      return;
    }
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.add(new THREE.HemisphereLight(0xcfd8ea, 0x202028, 1.3));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(2, 3, 4);
    this.scene.add(key);
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.01, 50);
    this.pivot = new THREE.Group();
    this.scene.add(this.pivot);
    this._loop = this._loop.bind(this);
    this._raf = requestAnimationFrame(this._loop);
  }

  show(body, family = "") {
    if (this.failed) return;
    this.pivot.clear();
    const sk = buildFixture(body, family);
    const moving = body && body.moving;
    if (!moving && !sk.standing) sk.tilt.rotation.x = 0.5;
    for (const lens of sk.lenses) lens.emissive.setRGB(0.9, 0.85, 0.7).multiplyScalar(1.5);
    this.pivot.add(sk.root);
    const box = new THREE.Box3().setFromObject(sk.root);
    const c = box.getCenter(new THREE.Vector3());
    sk.root.position.sub(c);
    const size = box.getSize(new THREE.Vector3()).length();
    this.camera.position.set(0, size * 0.35, size * 1.9);
    this.camera.lookAt(0, 0, 0);
    this.sk = sk;
  }

  _loop() {
    if (this.destroyed) return;
    this._raf = requestAnimationFrame(this._loop);
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (!w || !h) return;
    if (this.renderer.domElement.width !== Math.round(w * this.renderer.getPixelRatio())) {
      this.renderer.setSize(w, h, false);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
    }
    this.pivot.rotation.y += 0.008;
    if (this.sk && this.sk.pan && this.sk.moving !== false) {
      const t = performance.now() / 1000;
      if (this.sk.tilt && this.sk.pan && this.pivot.children.length) {
        this.sk.tilt.rotation.x = 0.5 + Math.sin(t * 0.9) * 0.35;
      }
    }
    this.renderer.render(this.scene, this.camera);
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this._raf);
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
    }
  }
}
