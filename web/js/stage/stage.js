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
import { buildVenue, hitDistance, cutaway } from "./venue.js";
import { SfxSystem } from "./sfx.js";
import { Shadows } from "./shadows.js";
import {
  LIGHTS, MAX_LIGHTS, beamGeometry, beamMaterial, glowMap, GoboAtlas,
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

const EMPTY_LOOK = { a: 0, r: 1, g: 1, b: 1, pan: null, tilt: null, beam: {}, hz: 0, mv: null, fx: null };

function lookFrom(row, scratch) {
  if (!row) return { ...EMPTY_LOOK, beam: {} };
  const c = hexLinear(row.hex, scratch);
  return {
    a: +row.a || 0, r: c.r, g: c.g, b: c.b,
    pan: typeof row.pan === "number" ? row.pan : null,
    tilt: typeof row.tilt === "number" ? row.tilt : null,
    deg: row.deg || null,
    beam: row.beam || {},
    hz: +row.hz || 0,            // the real strobe rate, 0 = steady
    mv: row.mv || null,          // {p, t: full-travel seconds, s: speed 0..1}
    fx: row.fx || null,          // an effect firing: {fire, fog, laser, pattern...}
    // each head of a multi-head light: its own colour and tilt
    cells: Array.isArray(row.cells) ? row.cells.map((c) => {
      const cc = hexLinear(c.hex, scratch);
      return { r: cc.r, g: cc.g, b: cc.b, tilt: typeof c.tilt === "number" ? c.tilt : null };
    }) : null,
  };
}

function mixLook(a, b, t) {
  // Only what the NEW look has: a value the engine stopped sending (a
  // strobe that was released) must go, not linger from the old look.
  const beam = {};
  for (const k of Object.keys(b.beam)) {
    const x = a.beam[k], y = b.beam[k];
    beam[k] = x === undefined ? y : lerp(x, y, t);
  }
  // pan / tilt go straight to the target: the motor model (_drive) gives
  // the travel its real speed.  Easing them as well restarted from a
  // standstill on every look update, so a head following a moving target
  // (roam, the floor map, an XY pad) hardly moved at all.
  const ang = (x, y) => (y === null ? x : y);
  return {
    a: lerp(a.a, b.a, t), r: lerp(a.r, b.r, t), g: lerp(a.g, b.g, t),
    b: lerp(a.b, b.b, t), pan: ang(a.pan, b.pan), tilt: ang(a.tilt, b.tilt),
    deg: b.deg || a.deg, beam, hz: b.hz, mv: b.mv || a.mv, fx: b.fx,
    cells: b.cells ? b.cells.map((c, i) => {
      const o = (a.cells && a.cells[i]) || c;
      return { r: lerp(o.r, c.r, t), g: lerp(o.g, c.g, t), b: lerp(o.b, c.b, t),
        tilt: c.tilt === null ? null : c.tilt };
    }) : null,
  };
}

// Seconds for a FULL pan / tilt at top speed, by type, until a fixture
// model is calibrated against the real light (Calibrate movement speed).
const TRAVEL = {
  moving_beam: [2.2, 1.3], moving_spot: [3.0, 1.8], moving_wash: [3.2, 1.9],
  moving_hybrid: [2.8, 1.7], moving_bar: [3.0, 1.5],
  scanner: [0.35, 0.3],                      // a mirror, not a head: very quick
};

/**
 * One axis of a moving head's motor: accelerates, cruises at the
 * fixture's top speed (slowed by its speed channel), and brakes into the
 * target - instead of snapping there in a tenth of a second.  Positions
 * are the look's 0..1 across the axis's travel.
 */
function motorStep(m, target, full, speed, dt) {
  if (target === null) { m.v = 0; return m.x; }          // not driven: hold
  if (m.x === null) { m.x = target; m.v = 0; return m.x; } // first sight
  const slow = 1 + 14 * Math.pow(Math.max(0, Math.min(1, speed || 0)), 1.6);
  // A full sweep takes `total` seconds INCLUDING speeding up and braking
  // (that is what a stopwatch measures): travel/vmax + ramp = total.
  const total = Math.max(0.2, full * slow);
  const ramp = Math.min(0.35, 0.12 + total * 0.08, total * 0.45);
  const vmax = 1 / (total - ramp);
  const acc = vmax / ramp;
  const dist = target - m.x;
  if (Math.abs(dist) < 1e-4 && Math.abs(m.v) < vmax * 0.02) { m.x = target; m.v = 0; return m.x; }
  const want = Math.sign(dist) * Math.min(vmax, Math.sqrt(2 * acc * Math.abs(dist)));
  const dv = want - m.v;
  m.v += Math.sign(dv) * Math.min(Math.abs(dv), acc * dt);
  const step = m.v * dt;
  m.x = Math.abs(step) >= Math.abs(dist) ? target : m.x + step;
  if (m.x === target) m.v = 0;
  return m.x;
}

export class Stage {
  constructor(container, opts = {}) {
    this.el = container;
    this.opts = opts;
    // the fixtures' own gobo pictures, fetched as lights first show them
    this.gobos = opts.loadGobo ? new GoboAtlas(opts.loadGobo) : null;
    if (this.gobos) this.gobos.onReady = () => { this.dirty = true; };
    this.fixtures = new Map();            // head_no -> instance
    this.selected = new Set();
    this.options = { haze: 0.6, bloom: true, people: true, labels: true, house: 0.35,
      quality: "auto", zones: false, dance: true, shadows: true };
    this.t0 = performance.now();
    this.dirty = true;
    this.venueSig = "";
    this.venueData = null;
    this.room = { w: 10, d: 8, h: 7, x0: -5, x1: 5, z0: -1, z1: 7 };
    this.stageFront = 6;
    this.planes = [];
    this.boxes = [];
    this.built = null;
    this.q = { cap: 1.5, ratio: 1.5, ema: 16, slowSince: 0, fastSince: 0, last: 0 };
    this.lastRender = 0;
    this.tags = new Map();
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
    this.q.cap = Math.min(window.devicePixelRatio || 1, 1.5);
    this.q.ratio = this.q.cap;
    renderer.setPixelRatio(this.q.ratio);
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
    this.sfx = new SfxSystem(scene);         // confetti, CO2, flame, fog, lasers
    this.shadows = new Shadows(renderer, scene);   // the crowd and the stage block the beams
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.hemi = new THREE.HemisphereLight(0xb8c4dc, 0x14151a, 0.22);
    scene.add(this.hemi);
    const key = new THREE.DirectionalLight(0xdfe7ff, 0.35);
    key.position.set(4, 9, 12);
    scene.add(key);
    this.key = key;

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.05, 400);
    this.camera.position.set(0, 4, 18);
    this.controls = new OrbitControls(this.camera, renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.09;
    this.controls.screenSpacePanning = true;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.controls.minDistance = 0.6;
    this.controls.maxDistance = 120;
    this.controls.addEventListener("start", () => { this._userMoved = true; });
    this.controls.addEventListener("change", () => {
      this.dirty = true;
      this.camMoved = true;
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
    this._applyHouse();
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
    this.renderer.setPixelRatio(this.q.ratio);
    this.renderer.setSize(w, h, false);
    this.composer.setPixelRatio(this.q.ratio);
    this.composer.setSize(w, h);
    this.bloom.setSize(Math.ceil(w * this.q.ratio / 2), Math.ceil(h * this.q.ratio / 2));
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  // ----------------------------------------------------------------- venue
  buildVenue(venue, fixtures) {
    const auto = !venue || venue.auto || !(venue.room && venue.room.width);
    const vsig = JSON.stringify(auto ? null : { ...venue, cameras: null });
    const fsig = auto ? JSON.stringify(fixtures.map((f) => [Math.round(f.x * 2), Math.round(f.y * 2),
      Math.round(f.z * 2), f.stance || f.kind])) : "";
    const sig = vsig + "|" + fsig;
    if (sig === this.venueSig) return;
    this.venueSig = sig;
    this.venueGroup.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
    this.venueGroup.clear();
    const built = buildVenue(auto ? null : venue, fixtures, {
      people: this.options.people, zones: this.options.zones,
      loadUnderlay: this.opts.loadUnderlay, onChange: () => { this.dirty = true; } });
    this.built = built;
    this.venueGroup.add(built.group);
    this.room = { ...built.room };
    this.stageFront = built.stageFront;
    // a new room size: the view goes back to the front of THIS room (the
    // camera was placed once, for the default room - 24 m back from a 3 m
    // room)
    const R = this.room;
    const rkey = [R.w, R.d, R.h, R.x0, R.z0].map((v) => Math.round(v * 10)).join(",");
    const firstRoom = this._roomKey === undefined;
    const roomChanged = !firstRoom && rkey !== this._roomKey;
    // the page opened on the stand-in room: the real one is a jump, not a fly
    const jump = firstRoom || this._roomAuto || document.hidden;
    this._roomKey = rkey;
    this._roomAuto = auto;
    this.planes = built.planes;
    this.boxes = built.boxes;
    this.segments = built.segments;
    this.people = built.people;
    cutaway(built, this.camera.position);
    this._underlayVisibility();
    this.dirty = true;
    if (this.editor) this.editor.refresh();
    // a drawn room that changed size: back to its front - but not for the
    // stand-in room that follows the lights, and not while arranging
    if ((firstRoom && !this._userMoved) || (roomChanged && !auto && !this.editing)) this.view("front", jump);
    if (this.opts.onVenueBuilt) this.opts.onVenueBuilt(built);
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
      const sig = [f.body && f.body.type, f.body && f.body.brand, f.model, f.mode, (f.body && f.body.cells) || 1,
        (f.body && f.body.heads) || 1].join("|");
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
    if (this.editor) this.editor.refresh();
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
    const hung = f.stance ? f.stance === "hang" : (f.kind || (f.y >= 2 ? "truss" : "floor")) === "truss";
    inst.hung = hung;
    inst.holder.position.set(+f.x || 0, +f.y || 0, +f.z || 0);
    const flip = inst.sk.native === "hung" ? !hung : hung;
    inst.sk.root.rotation.set(0, 0, flip ? Math.PI : 0);
    inst.pick.position.y = (hung ? -1 : 1) * inst.sk.height / 2;
    inst.staticAim = null;
  }

  /** Where a light with no pan/tilt points: its stored aim, or a sensible spot. */
  _aimStatic(inst) {
    const body = inst.data.body || {};
    if (body.moving) return;                         // driven by the feed
    const f = inst.data;
    if (Array.isArray(f.rot) && f.rot.length === 2) {
      const yaw = f.rot[0] * DEG, pitch = f.rot[1] * DEG;
      this._setStaticDir(inst, new THREE.Vector3(Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch),
        Math.cos(yaw) * Math.cos(pitch)));
      return;
    }
    const { h } = this.room;
    const front = this.stageFront;
    const p = new THREE.Vector3(+f.x || 0, +f.y || 0, +f.z || 0);
    let target;
    if (body.type === "tube") {
      target = new THREE.Vector3(p.x * 1.2, p.y, p.z + 6);
    } else if (body.type === "followspot") {
      target = new THREE.Vector3(0, 1.4, front * 0.6);
    } else if (body.type === "blinder") {
      target = new THREE.Vector3(p.x, 1.6, p.z + 8);
    } else if (!inst.hung) {
      // floor lights: uplight the back wall from the rear of the stage,
      // otherwise lift into the room
      target = (p.z < front - 1.2 || body.type === "cyc")
        ? new THREE.Vector3(p.x * 1.1, h * 0.8, p.z - 2.5)
        : new THREE.Vector3(p.x * 0.9, h * 0.75, p.z + 3);
    } else if (p.z > front + 0.5) {
      target = new THREE.Vector3(p.x * 0.8, 0, p.z + 2.5);
    } else {
      target = new THREE.Vector3(p.x * 0.9, 0, Math.max(p.z + 1.2, front * 0.6));
    }
    this._setStaticDir(inst, target.sub(p).normalize());
  }

  _setStaticDir(inst, worldDir) {
    const sk = inst.sk;
    if (sk.gdtf) return;
    inst.holder.updateMatrixWorld(true);
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
      inst.settled = false;
    }
    this.dirty = true;
    return true;
  }

  setSelected(list) {
    this.selected = new Set((list || []).map(Number));
    this.dirty = true;
  }

  setOptions(o) {
    const before = { ...this.options };
    Object.assign(this.options, o || {});
    if (this.people) this.people.visible = !!this.options.people;
    if (this.built && this.built.zones) this.built.zones.visible = !!this.options.zones;
    if (before.house !== this.options.house) this._applyHouse();
    if (before.quality !== this.options.quality) {
      const dpr = window.devicePixelRatio || 1;
      this.q.cap = this.options.quality === "high" ? Math.min(dpr, 2)
        : this.options.quality === "fast" ? Math.min(dpr, 0.75) : Math.min(dpr, 1.5);
      this.q.ratio = this.q.cap;
      this.resize();
    }
    this.dirty = true;
  }

  /** House lights: how much of the room you see with the rig dark. */
  _applyHouse() {
    const k = Math.max(0, Math.min(1, this.options.house));
    LIGHTS.uAmbient.value.setRGB(0.5, 0.53, 0.6).multiplyScalar(0.03 + Math.pow(k, 1.1) * 1.9);
    this.hemi.intensity = 0.12 + k * 1.5;
    this.key.intensity = 0.15 + k * 1.1;
    this.scene.background.setRGB(0.012, 0.014, 0.02).lerp(new THREE.Color(0.05, 0.056, 0.07), k);
    this.dirty = true;
  }

  // ---------------------------------------------------------------- camera
  cameraState() {
    return { pos: this.camera.position.toArray(), target: this.controls.target.toArray() };
  }

  setCamera(s) {
    if (!s || !s.pos || !s.target) return;
    this.povHead = null;
    this.camera.position.fromArray(s.pos);
    this.controls.target.fromArray(s.target);
    this.controls.update();
    this.dirty = true;
  }

  view(name, instant = false) {
    const R = this.room;
    const front = this.stageFront;
    const t = new THREE.Vector3(0, Math.min(2.4, R.h * 0.4), Math.max(R.z0 + 1, front * 0.55));
    const house = front + Math.max(6, (R.z1 - front) * 0.72);
    const at = {
      front: [0, Math.max(3.2, R.h * 0.8), R.z1 + Math.max(3, R.d * 0.18)],
      left: [R.x0 - 5, Math.min(R.h, 5), (R.z0 + R.z1) / 2],
      right: [R.x1 + 5, Math.min(R.h, 5), (R.z0 + R.z1) / 2],
      back: [0, Math.min(R.h * 0.7, 5), R.z0 - 5],
      top: [R.cx || 0, Math.max(R.w * 0.9, R.d) * 1.55 + R.h, (R.z0 + R.z1) / 2 + 0.01],
      house: [0, Math.min(R.h - 0.5, 3.4), R.z1 - 1],
      overview: [R.x1 + R.w * 0.35, R.h + Math.max(R.w, R.d) * 0.45, R.z1 + R.d * 0.35],
    }[name];
    if (name === "top" || name === "overview") t.set(R.cx || 0, 0, (R.z0 + R.z1) / 2);
    this._flyTo(new THREE.Vector3(...(at || [0, 4, house])), t, instant);
  }

  /** Eye level on the dance floor (or the middle of the audience). */
  viewCrowd() {
    const v = (this.built && this.built.venue) || {};
    const zone = (v.zones || []).find((z) => z.kind === "dancefloor")
      || (v.zones || []).find((z) => z.kind === "standing" || z.kind === "seating");
    let x = 0, z = this.stageFront + 6;
    if (zone) {
      x = zone.points.reduce((a, q) => a + q[0], 0) / zone.points.length;
      z = zone.points.reduce((a, q) => a + q[1], 0) / zone.points.length;
    }
    this._flyTo(new THREE.Vector3(x, 1.65, z), new THREE.Vector3(x * 0.5, 2.6, Math.max(0.5, this.stageFront * 0.5)), false);
  }

  /** From the DJ mark (or the stage centre), looking out at the room. */
  viewStage() {
    const v = (this.built && this.built.venue) || {};
    const mark = (v.objects || []).find((o) => o.kind === "mark");
    const st = v.stage;
    const x = mark ? mark.x : st ? st.x : 0;
    const z = mark ? mark.z : st ? st.z + st.depth * 0.4 : 1;
    const y = (mark ? mark.y || 0 : st ? st.height : 0) + 1.7;
    this._flyTo(new THREE.Vector3(x, y, z), new THREE.Vector3(x, 1.4, this.room.z1 - 1), false);
  }

  /** Look down a light's beam, the way you would check its focus. */
  lookThrough(head) {
    const inst = this.fixtures.get(Number(head));
    if (!inst) return false;
    this.rigGroup.updateMatrixWorld(true);
    const b = inst.beams[0];
    const origin = new THREE.Vector3(), dir = new THREE.Vector3(0, -1, 0);
    if (b) {
      b.em.node.getWorldPosition(origin);
      dir.copy(b.em.dir).transformDirection(b.em.node.matrixWorld).normalize();
    } else {
      origin.copy(inst.holder.position);
    }
    const eye = origin.clone().addScaledVector(dir, 0.12);
    this._flyTo(eye, origin.clone().addScaledVector(dir, 6), false);
    this.povHead = inst.head;                        // its own beam would fill the view
    return true;
  }

  _flyTo(pos, target, instant) {
    this.povHead = null;
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

  /** Heads whose light sits inside a screen rectangle (client px). */
  headsInRect(x0, y0, x1, y1) {
    const r = this.renderer.domElement.getBoundingClientRect();
    const [lx, hx] = [Math.min(x0, x1), Math.max(x0, x1)];
    const [ly, hy] = [Math.min(y0, y1), Math.max(y0, y1)];
    const v = new THREE.Vector3();
    const out = [];
    for (const inst of this.fixtures.values()) {
      if (!inst.pick) continue;
      inst.pick.getWorldPosition(v);
      v.project(this.camera);
      if (v.z > 1) continue;                                  // behind the camera
      const sx = r.left + (v.x + 1) / 2 * r.width, sy = r.top + (1 - v.y) / 2 * r.height;
      if (sx >= lx && sx <= hx && sy >= ly && sy <= hy) out.push(inst.head);
    }
    return out;
  }

  _wirePointer() {
    const dom = this.renderer.domElement;
    let down = null;
    // Shift-drag draws a box: every light inside it is selected (the
    // orbit waits).  Captured first, so the orbit never starts.
    let box = null;
    dom.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0 || !ev.shiftKey || this.editing || this.pickOnce || !this.opts.onBox) return;
      const wrap = dom.parentElement;
      const el = document.createElement("div");
      el.className = "stage-box";
      wrap.append(el);
      box = { x: ev.clientX, y: ev.clientY, el, moved: false };
      this.controls.enabled = false;
    }, true);
    dom.addEventListener("pointermove", (ev) => {
      if (!box) return;
      const r = dom.parentElement.getBoundingClientRect();
      box.moved = box.moved || Math.hypot(ev.clientX - box.x, ev.clientY - box.y) > 5;
      Object.assign(box.el.style, {
        left: `${Math.min(ev.clientX, box.x) - r.left}px`, top: `${Math.min(ev.clientY, box.y) - r.top}px`,
        width: `${Math.abs(ev.clientX - box.x)}px`, height: `${Math.abs(ev.clientY - box.y)}px`,
      });
    });
    const endBox = (ev) => {
      if (!box) return false;
      const b = box;
      box = null;
      b.el.remove();
      this.controls.enabled = true;
      if (!b.moved || !ev) return false;
      this.opts.onBox(this.headsInRect(b.x, b.y, ev.clientX, ev.clientY), { add: ev.ctrlKey || ev.metaKey });
      return true;
    };
    window.addEventListener("pointerup", (ev) => { if (endBox(ev)) down = null; }, true);
    dom.addEventListener("pointercancel", () => endBox(null));
    dom.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      const ed = this.editor;
      const onGizmo = ed && this.editing && !ed.drawing && ed.tc.object && ed.tc.axis;
      down = { x: ev.clientX, y: ev.clientY, gizmo: !!onGizmo };
    });
    dom.addEventListener("pointermove", (ev) => {
      if (down) return;
      const inst = this._hit(ev);
      const h = inst ? inst.head : null;
      if (h !== this.hover) { this.hover = h; this.dirty = true; }
      dom.style.cursor = this.editing && this.editor && this.editor.drawing ? "crosshair"
        : inst ? "pointer" : "";
    });
    const up = (ev) => {
      if (!down) return;
      const d0 = down;
      down = null;
      if (d0.gizmo || (this.editor && this.editor.busy)) return;
      if (Math.hypot(ev.clientX - d0.x, ev.clientY - d0.y) > 5) return;   // that was an orbit
      if (this.pickOnce) {
        const cb = this.pickOnce;
        this.pickOnce = null;
        dom.classList.remove("picking");
        const p = this.surfacePoint(ev);
        if (p) cb(p);
        return;
      }
      if (this.editing && this.editor) { this.editor.click(ev); return; }
      const inst = this._hit(ev);
      if (inst && this.opts.onPick) {
        this.opts.onPick(inst.head, { shift: ev.shiftKey, toggle: ev.ctrlKey || ev.metaKey });
      }
    };
    dom.addEventListener("pointerup", up);
    dom.addEventListener("pointercancel", () => { down = null; });
    dom.addEventListener("dblclick", (ev) => {
      if (this.editing && this.editor && this.editor.drawing) { this.editor.finishDraw(); return; }
      const inst = this._hit(ev);
      if (inst) this.frame([inst.head]);
    });
  }

  /** The traced floor plan is a drawing aid: shown while arranging. */
  _underlayVisibility() {
    const u = this.built && this.built.underlay;
    if (!u) return;
    const v = this.built.venue.underlay || {};
    u.visible = this.editing && v.show !== false;
    this.dirty = true;
  }

  /** Follow mode: press and drag in the view and `cb(point)` gets every
   *  surface point the pointer crosses (the lights follow it); a ring marks
   *  the spot.  Right-drag still orbits.  `follow(null)` ends it. */
  follow(cb, onEnd) {
    const dom = this.renderer.domElement;
    if (!this._followWired) {
      this._followWired = true;
      let drag = false;
      const at = (ev) => {
        const p = this.surfacePoint(ev);
        if (!p || !this._follow) return;
        this._followMark(p);
        this._follow.cb(p, ev.type === "pointerup");
      };
      dom.addEventListener("pointerdown", (ev) => {
        if (!this._follow || ev.button !== 0 || this.editing) return;
        ev.stopImmediatePropagation();
        this.controls.enabled = false;
        drag = true;
        try { dom.setPointerCapture(ev.pointerId); } catch { /* old browsers */ }
        at(ev);
      }, true);
      dom.addEventListener("pointermove", (ev) => {
        if (!this._follow) return;
        if (drag) { ev.stopImmediatePropagation(); at(ev); }
      }, true);
      const end = (ev) => {
        if (!drag) return;
        drag = false;
        this.controls.enabled = true;
        if (ev && ev.type === "pointerup") { ev.stopImmediatePropagation(); at(ev); }
      };
      dom.addEventListener("pointerup", end, true);
      dom.addEventListener("pointercancel", end, true);
      window.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && this._follow) this.follow(null);
      }, true);
    }
    const prev = this._follow;
    this._follow = cb ? { cb, onEnd } : null;
    dom.classList.toggle("following", !!cb);
    if (!cb) {
      if (this._followRing) { this._followRing.visible = false; this.dirty = true; }
      if (prev && prev.onEnd) prev.onEnd();
    }
  }

  _followMark(p) {
    if (!this._followRing) {
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.35, 0.5, 40).rotateX(-Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.9, depthWrite: false, depthTest: false, side: THREE.DoubleSide }));
      const dot = new THREE.Mesh(new THREE.CircleGeometry(0.08, 20).rotateX(-Math.PI / 2), ring.material);
      ring.add(dot);
      ring.renderOrder = 999;
      dot.renderOrder = 999;
      this.scene.add(ring);
      this._followRing = ring;
    }
    this._followRing.position.set(p.x, p.y + 0.02, p.z);
    this._followRing.visible = true;
    this.dirty = true;
  }

  /** The next click in the view reports the surface point under it. */
  pickPoint(cb) {
    this.pickOnce = cb;
    const dom = this.renderer.domElement;
    dom.classList.add("picking");
    const esc = (e) => {
      if (e.key !== "Escape") return;
      this.pickOnce = null;
      dom.classList.remove("picking");
      window.removeEventListener("keydown", esc, true);
    };
    window.addEventListener("keydown", esc, true);
  }

  /** Where a ray from the pointer lands: stage, riser, object or floor. */
  surfacePoint(ev) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const targets = [];
    if (this.built) {
      this.built.group.traverse((o) => {
        if (!o.isMesh || !o.visible || o.userData.crowd || o.userData.performer || o.userData.zone) return;
        const k = o.userData.venueKind;
        if (k === "floor" || k === "stage" || (o.userData.venueId && k !== "underlay" && !["truss", "pipe", "tower", "ladder", "stand", "base"].includes(k))) targets.push(o);
      });
    }
    const hit = this.raycaster.intersectObjects(targets, false)[0];
    if (hit) return hit.point.clone();
    return this.raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3());
  }

  /** Arrange mode: the editor owns clicks and shows its gizmo. */
  setEditing(on, editor) {
    if (editor) this.editor = editor;
    this.editing = !!on;
    if (this.editor) this.editor.setEnabled(this.editing);
    this._underlayVisibility();
    this.dirty = true;
  }

  _wireKeys() {
    const dom = this.renderer.domElement;
    const views = { 1: "front", 2: "left", 3: "right", 4: "back", 5: "top", 6: "overview" };
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
    let motion = false;

    if (this.walking) this._walkStep(now);
    if (this.fly) {
      const t = smooth((now - this.fly.start) / this.fly.dur);
      this.camera.position.lerpVectors(this.fly.p0, this.fly.p1, t);
      this.controls.target.lerpVectors(this.fly.t0, this.fly.t1, t);
      if (t >= 1) this.fly = null;
      motion = true;
      this.camMoved = true;
    }
    if (this.walking) this.camMoved = true;               // the walker steers the camera, not the orbit
    else if (this.controls.update()) motion = true;
    if (this.camMoved) {
      cutaway(this.built, this.camera.position);
      this.camMoved = false;
      this.dirty = true;
    }

    for (const inst of this.fixtures.values()) {
      // entrance: a light that was just patched drops into place
      const age = (now - inst.born) / 420;
      if (age < 1) {
        const k = 1 - Math.pow(1 - age, 3);
        inst.sk.root.scale.setScalar(0.2 + 0.8 * k);
        inst.sk.root.position.y = (inst.hung ? -1 : 1) * 0.5 * (1 - k);
        motion = true;
      } else if (inst.sk.root.scale.x !== 1) {
        inst.sk.root.scale.setScalar(1);
        inst.sk.root.position.y = 0;
      }
      if (inst.dur > 0 && now - inst.t0 < inst.dur) motion = true;
      if (inst.moving) motion = true;          // a head still travelling
      if (this.sfxBusy) motion = true;          // confetti still falling
    }
    // A lit rig still animates (strobe, haze drift, the crowd), but 30
    // frames a second is plenty for that; a moving one gets every frame.
    if (!this.dirty && !motion && !(this.anyLit && now - this.lastRender > 32)) return;

    const time = (now - this.t0) / 1000;
    LIGHTS.uTime.value = time;
    LIGHTS.uBounce.value = this.options.dance ? 1 : 0;
    const lights = [];
    let lit = 0;
    for (const inst of this.fixtures.values()) {
      if (!inst.settled) {
        // from where it is now to the new look, landing by t0 + dur
        // whatever the frame rate: easing from inst.from restarted on every
        // update, so when frames were slower than the looks came in (a slow
        // GPU, a big rig) a rainbow or chase never got anywhere and the
        // beams stayed the colour they started
        const end = inst.t0 + inst.dur;
        // progress = the real time since the last frame (up to 50 ms of it
        // from before the update): a slow frame that lands just after an
        // update still moves the light
        const from = Math.max(inst.mixAt || 0, inst.t0 - 50);
        const k = inst.dur <= 0 || now >= end ? 1 : Math.max(0, (now - from) / (end - from));
        inst.cur = mixLook(inst.cur, inst.to, k);
        inst.mixAt = now;
        inst.settled = k >= 1;
      }
      if (this._drive(inst, time, lights)) lit++;
    }
    this.anyLit = lit > 0;
    this.rigGroup.updateMatrixWorld(true);
    for (const inst of this.fixtures.values()) this._updateBeams(inst, time, lights);
    const dtS = this._sfxLast ? Math.min(0.1, (now - this._sfxLast) / 1000) : 0;
    this._sfxLast = now;
    this.sfxBusy = this.sfx.update(dtS, time, this.fixtures,
      (o, d) => hitDistance(this.planes, this.boxes, o, d, 30, this.segments));
    this._uploadLights(lights);
    // shadows for the brightest beams, unless the view is kept light
    if (this.options.shadows !== false && this.options.quality !== "fast" && this.q.ratio > 0.6) {
      this.shadows.update(LIGHTS.uCount.value);
    } else this.shadows.off();
    this._drawScreens(now);

    this.dirty = false;
    this._adapt(now);
    this.lastRender = now;
    this.bloom.enabled = !!this.options.bloom && this.q.ratio > 0.55;
    this.composer.render();
    this._drawLabels();
    if (this.opts.onFrame) this.opts.onFrame(now);
  }

  /** A still of the view at print size (the long side `longSide` px), as
   *  a PNG data URL: the scene rendered again at a higher resolution. */
  photo(longSide = 3840) {
    const w = Math.max(1, this.el.clientWidth), h = Math.max(1, this.el.clientHeight);
    const keep = this.q.ratio;
    this.q.ratio = Math.min(4, Math.max(1, longSide / Math.max(w, h)));
    this.resize();
    this.bloom.enabled = !!this.options.bloom;
    this.composer.render();
    const url = this.renderer.domElement.toDataURL("image/png");
    this.q.ratio = keep;
    this.resize();
    this.dirty = true;
    return url;
  }

  /** Walk the room at eye height: W A S D / arrows to move (Shift runs),
   *  drag to look round, Esc (or walk(false)) to stop.  Stays inside the
   *  walls. */
  walk(on = true, onEnd = null) {
    const dom = this.renderer.domElement;
    if (!on) {
      if (!this.walking) return;
      const w = this.walking;
      this.walking = null;
      window.removeEventListener("keydown", w.kd, true);
      window.removeEventListener("keyup", w.ku, true);
      dom.removeEventListener("pointerdown", w.pd, true);
      window.removeEventListener("pointermove", w.pm, true);
      window.removeEventListener("pointerup", w.pu, true);
      dom.classList.remove("walking");
      this.controls.enabled = true;
      this.controls.target.copy(this.camera.position).add(new THREE.Vector3(0, 0, -2).applyEuler(new THREE.Euler(w.pitch, w.yaw, 0, "YXZ")));
      this.controls.update();
      if (w.onEnd) w.onEnd();
      return;
    }
    if (this.walking) return;
    this.povHead = null;
    const v = (this.built && this.built.venue) || {};
    const zone = (v.zones || []).find((z) => z.kind === "dancefloor") || (v.zones || []).find((z) => z.kind === "standing");
    const R = this.room;
    let x = (R.cx || 0), z = Math.min(R.z1 - 1, (this.stageFront || 0) + 4);
    if (zone) {
      x = zone.points.reduce((a, q) => a + q[0], 0) / zone.points.length;
      z = zone.points.reduce((a, q) => a + q[1], 0) / zone.points.length;
    }
    this.fly = null;
    this.camera.position.set(x, 1.7, z);
    const w = { keys: new Set(), yaw: 0, pitch: 0.12, drag: null, last: performance.now(), onEnd };
    const keyOf = (e) => ({ ArrowUp: "w", ArrowDown: "s", ArrowLeft: "a", ArrowRight: "d" }[e.key] || e.key.toLowerCase());
    w.kd = (e) => {
      if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
      if (e.key === "Escape") { this.walk(false); e.stopPropagation(); return; }
      const k = keyOf(e);
      if ("wasd".includes(k) && k.length === 1) { w.keys.add(k); e.preventDefault(); e.stopPropagation(); }
      w.run = e.shiftKey;
    };
    w.ku = (e) => { w.keys.delete(keyOf(e)); w.run = e.shiftKey; };
    w.pd = (e) => { w.drag = { x: e.clientX, y: e.clientY }; e.stopImmediatePropagation(); };
    w.pm = (e) => {
      if (!w.drag) return;
      w.yaw -= (e.clientX - w.drag.x) * 0.004;
      w.pitch = Math.max(-1.2, Math.min(1.2, w.pitch - (e.clientY - w.drag.y) * 0.004));
      w.drag = { x: e.clientX, y: e.clientY };
      this.dirty = true;
    };
    w.pu = () => { w.drag = null; };
    window.addEventListener("keydown", w.kd, true);
    window.addEventListener("keyup", w.ku, true);
    dom.addEventListener("pointerdown", w.pd, true);
    window.addEventListener("pointermove", w.pm, true);
    window.addEventListener("pointerup", w.pu, true);
    dom.classList.add("walking");
    this.controls.enabled = false;
    this.walking = w;
    this.dirty = true;
  }

  _walkStep(now) {
    const w = this.walking;
    const dt = Math.min(0.1, (now - w.last) / 1000);
    w.last = now;
    const speed = (w.run ? 5 : 2.2) * dt;
    const fwd = (w.keys.has("w") ? 1 : 0) - (w.keys.has("s") ? 1 : 0);
    const side = (w.keys.has("d") ? 1 : 0) - (w.keys.has("a") ? 1 : 0);
    const p = this.camera.position;
    if (fwd || side) {
      p.x += (-Math.sin(w.yaw) * fwd + Math.cos(w.yaw) * side) * speed;
      p.z += (-Math.cos(w.yaw) * fwd - Math.sin(w.yaw) * side) * speed;
      const R = this.room;
      p.x = Math.max(R.x0 + 0.3, Math.min(R.x1 - 0.3, p.x));
      p.z = Math.max(R.z0 + 0.3, Math.min(R.z1 - 0.3, p.z));
      this.dirty = true;
    }
    this.camera.rotation.set(w.pitch, w.yaw, 0, "YXZ");
  }

  /** LED screens: "the lights" draws every light's colour as a tile (a
   *  pixel-map mirror of the rig, in number order); a clip keeps the view
   *  rendering while it plays. */
  _drawScreens(now) {
    const list = this.built && this.built.screens;
    if (!list || !list.length) return;
    if (list.some((m) => m.material.userData.video)) this.dirty = true;
    if (now - (this._scrLast || 0) < 60) return;
    this._scrLast = now;
    const heads = [...this.fixtures.values()].sort((a, b) => a.head - b.head);
    const tiles = [];
    for (const inst of heads) {
      const L = inst.cur || {};
      const a = Math.min(1, inst.level || 0);
      if (L.cells && L.cells.length > 1) for (const c of L.cells) tiles.push([c.r, c.g, c.b, a]);
      else tiles.push([L.r || 0, L.g || 0, L.b || 0, a]);
    }
    for (const m of list) {
      const cv = m.material.userData.canvas;
      if (!cv) continue;
      const ctx = cv.getContext("2d");
      const W = cv.width, H = cv.height;
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, W, H);
      const n = tiles.length;
      if (n) {
        const cols = Math.max(1, Math.ceil(Math.sqrt(n * W / H))), rows = Math.ceil(n / cols);
        const tw = W / cols, th = H / rows, gap = Math.max(1, Math.min(tw, th) * 0.06);
        tiles.forEach(([r, g, b, a], i) => {
          const k = 255 * Math.min(1, a * 1.2);
          ctx.fillStyle = `rgb(${Math.min(255, r * k) | 0},${Math.min(255, g * k) | 0},${Math.min(255, b * k) | 0})`;
          ctx.fillRect((i % cols) * tw + gap / 2, Math.floor(i / cols) * th + gap / 2, tw - gap, th - gap);
        });
      }
      m.material.map.needsUpdate = true;
    }
  }

  /** Auto quality: shed resolution when frames run long, win it back later. */
  _adapt(now) {
    const q = this.q;
    const dt = now - (q.last || now);
    q.last = now;
    if (this.options.quality !== "auto" || dt <= 0 || dt > 200) return;
    q.ema = q.ema * 0.9 + dt * 0.1;
    if (q.ema > 28) {
      q.fastSince = 0;
      if (!q.slowSince) q.slowSince = now;
      else if (now - q.slowSince > 1200 && q.ratio > 0.5) {
        q.ratio = Math.max(0.5, +(q.ratio * 0.8).toFixed(2));
        q.slowSince = 0;
        q.ema = 18;
        this.resize();
      }
    } else if (q.ema < 17) {
      q.slowSince = 0;
      if (!q.fastSince) q.fastSince = now;
      else if (now - q.fastSince > 6000 && q.ratio < q.cap) {
        q.ratio = Math.min(q.cap, +(q.ratio * 1.15).toFixed(2));
        q.fastSince = 0;
        this.resize();
      }
    } else {
      q.slowSince = 0;
      q.fastSince = 0;
    }
  }

  /** Apply one head's look to its model.  Returns true when it emits. */
  _drive(inst, time, lights) {
    const L = inst.cur;
    const sk = inst.sk;
    const body = inst.data.body || {};
    const moving = body.moving;
    if (moving || sk.gdtf) {
      const deg = L.deg || {};
      const pr = deg.pan || DEFAULT_PAN;
      const tr = deg.tilt || DEFAULT_TILT;
      // the motor: real-speed travel toward where the desk says
      const m = inst.motor || (inst.motor = { pan: { x: null, v: 0 }, tilt: { x: null, v: 0 }, last: time });
      const dt = Math.max(0, Math.min(0.1, time - m.last));
      m.last = time;
      const mv = L.mv || {};
      const [dp, dtl] = TRAVEL[body.type] || [3.0, 1.8];
      const px = motorStep(m.pan, L.pan, mv.p || dp, mv.s, dt);
      const tx = motorStep(m.tilt, L.tilt, mv.t || dtl, mv.s, dt);
      inst.moving = (L.pan !== null && px !== L.pan) || (L.tilt !== null && tx !== L.tilt);
      const pan = px === null ? 0 : lerp(pr[0], pr[1], px) * DEG;
      const tilt = tx === null ? 0 : lerp(tr[0], tr[1], tx) * DEG;
      if (sk.gdtf) {
        if (moving) { sk.setPan(pan); sk.setTilt(tilt); }
      } else {
        sk.pan.rotation.y = pan;
        sk.tilt.rotation.x = tilt;
      }
      if (sk.cells) {
        // each head of a multi-head light tilts on its own (no motor model)
        sk.cells.forEach((c, k) => {
          const ct = L.cells && L.cells[k] && L.cells[k].tilt !== null ? L.cells[k].tilt : tx;
          c.tilt.rotation.x = ct === null ? 0 : lerp(tr[0], tr[1], ct) * DEG;
        });
      }
    }
    let a = L.a;
    if (L.hz > 0 && (time * L.hz) % 1 > 0.3) a = 0;      // a real strobe only
    inst.level = a;
    const col = this._c.setRGB(L.r, L.g, L.b);
    for (const lens of sk.lenses) {
      lens.emissive.copy(col).multiplyScalar(a * 7);
    }
    if (sk.cells) {
      sk.cells.forEach((c, k) => {
        const cc = L.cells && L.cells[k];
        c.lens.emissive.setRGB(cc ? cc.r : L.r, cc ? cc.g : L.g, cc ? cc.b : L.b).multiplyScalar(a * 7);
      });
    }
    if (sk.spin && a > 0.002) sk.spin.rotation.y = time * 0.9;   // a derby turns while lit
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
      const len = hitDistance(this.planes, this.boxes, b.origin, b.dir, 40, this.segments);
      const half = (angle / 2) * DEG;
      const r0 = Math.max(0.01, em.radius);
      const r1 = r0 + Math.tan(half) * len;
      b.mesh.position.copy(b.origin);
      b.mesh.quaternion.setFromUnitVectors(UP, b.dir);
      const u = b.mat.uniforms;
      u.uLen.value = len;
      u.uR0.value = r0;
      u.uR1.value = r1;
      const cc = em.cell !== undefined && L.cells ? L.cells[em.cell] : null;
      u.uColor.value.setRGB(cc ? cc.r : L.r, cc ? cc.g : L.g, cc ? cc.b : L.b);
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
      b.glow.material.color.setRGB(cc ? cc.r : L.r, cc ? cc.g : L.g, cc ? cc.b : L.b).multiplyScalar(0.35 + 1.8 * Math.pow(facing, 4) * a);

      if (inst.head === this.povHead) {            // looking down this beam
        b.mesh.visible = false;
        b.glow.visible = false;
      }
      const goboId = this._goboId(inst, goboV);
      const rot = (L.beam.gobo_rot || 0) > 0.03 ? time * (L.beam.gobo_rot - 0.03) * 6 : 0;
      const power = a * 9 * Math.min(4, Math.pow(26 / Math.max(angle, 2), 1.1)) / inst.beams.length;
      lights.push({
        pos: b.origin, dir: b.dir, r: L.r * power, g: L.g * power, b: L.b * power,
        cosO: Math.cos(half), cosI: Math.cos(half * lerp(0.72, 0.25, frost)),
        gobo: goboId, rot, weight: power * (L.r + L.g + L.b),
      });
    }
  }

  /** Which gobo a light shows: the picture in its file for the slot the
   *  channel is in (100 + atlas cell), open (0), or - when the file names
   *  no pictures, or one can't be had - a drawn pattern (1..7). */
  _goboId(inst, v) {
    const rows = inst.data.gobos;
    if (rows && rows.length && this.gobos) {
      const dmx = Math.round(v * 255);
      const row = rows.find((r) => dmx >= r[0] && dmx <= r[1]);
      if (!row) return 0;                       // between pictures: open, or a spin range
      const cell = this.gobos.cell(row[2]);
      if (cell >= 0) return 100 + cell;
      return cell === -1 ? 0 : 1 + (rows.indexOf(row) % 7);   // loading: open for a moment
    }
    return v > 0.06 ? 1 + (Math.floor(v * 7.99) % 7) : 0;
  }

  /**
   * Hand the brightest beams to the surface shader.  Beams that start
   * close together and point the same way (the cells of one bar, a row of
   * PARs on one look) are merged into one light first: the floor looks the
   * same, and every pixel of the room loops over far fewer lights.
   */
  _uploadLights(lights) {
    const groups = new Map();
    for (const l of lights) {
      const key = Math.round(l.pos.x / 1.2) + "," + Math.round(l.pos.y / 1.2) + "," + Math.round(l.pos.z / 1.2)
        + "," + Math.round(l.dir.x * 5) + "," + Math.round(l.dir.y * 5) + "," + Math.round(l.dir.z * 5)
        + "," + l.gobo + "," + Math.round(l.cosO * 50);
      const g = groups.get(key);
      if (!g) {
        groups.set(key, { pos: l.pos.clone().multiplyScalar(l.weight), dir: l.dir.clone().multiplyScalar(l.weight),
          r: l.r, g: l.g, b: l.b, cosO: l.cosO, cosI: l.cosI, gobo: l.gobo, rot: l.rot, weight: l.weight });
      } else {
        g.pos.addScaledVector(l.pos, l.weight);
        g.dir.addScaledVector(l.dir, l.weight);
        g.r += l.r; g.g += l.g; g.b += l.b;
        g.cosO = Math.min(g.cosO, l.cosO);
        g.cosI = Math.min(g.cosI, l.cosI);
        g.weight += l.weight;
      }
    }
    const list = [...groups.values()].sort((x, y) => y.weight - x.weight);
    const cap = this.options.quality === "fast" ? 10
      : this.options.quality === "high" ? MAX_LIGHTS
        : this.q.ratio < 0.75 ? 14 : 24;
    const n = Math.min(cap, list.length);
    for (let i = 0; i < n; i++) {
      const l = list[i];
      const w = l.weight || 1;
      LIGHTS.uPos.value[i].copy(l.pos).multiplyScalar(1 / w);
      LIGHTS.uDir.value[i].copy(l.dir).normalize();
      LIGHTS.uCol.value[i].set(l.r, l.g, l.b);
      LIGHTS.uCone.value[i].set(l.cosO, l.cosI, l.gobo, l.rot);
    }
    LIGHTS.uCount.value = n;
  }

  _drawLabels() {
    // rings mark a big selection; a cloud of tags would hide the rig
    const want = new Set(this.options.labels && this.selected.size <= 12 ? this.selected : []);
    if (this.options.labels && this.hover !== null && this.hover !== undefined) want.add(this.hover);
    const w = this.el.clientWidth, h = this.el.clientHeight;
    for (const [head, tag] of this.tags) {
      if (!want.has(head) || !this.fixtures.has(head)) { tag.remove(); this.tags.delete(head); }
    }
    const p = this._v2;
    // tags that would sit on top of one another are dropped (the hovered
    // light's always wins): a pile of overlapping names reads as nothing
    const placed = [];
    const order = [...want].sort((a, b) => (b === this.hover) - (a === this.hover) || a - b);
    for (const head of order) {
      const inst = this.fixtures.get(head);
      if (!inst) continue;
      p.copy(inst.holder.position);
      p.y += (inst.hung ? 0.25 : inst.sk.height + 0.12);
      p.project(this.camera);
      let tag = this.tags.get(head);
      if (!tag) {
        tag = document.createElement("div");
        this.labels.appendChild(tag);
        this.tags.set(head, tag);
      }
      const d = inst.data;
      const brand = d.body && d.body.brand !== "generic" ? d.body.brand_name + " " : "";
      // the full name only where there is room for it: the light under the
      // pointer, or a single selected light; otherwise just its number
      const full = head === this.hover || want.size === 1;
      const text = full ? `#${head}  ${brand}${d.model || ""}`.trim() : `#${head}`;
      const cls = "stage-tag" + (this.selected.has(head) ? " sel" : "");
      if (tag.textContent !== text) tag.textContent = text;
      if (tag.className !== cls) tag.className = cls;
      const sx = (p.x + 1) / 2 * w, sy = (1 - p.y) / 2 * h;
      const clash = placed.some(([x, y]) => Math.abs(x - sx) < (full ? 90 : 34) && Math.abs(y - sy) < 18);
      tag.style.display = p.z > 1 || clash ? "none" : "";
      if (!clash) placed.push([sx, sy]);
      tag.style.left = sx.toFixed(1) + "px";
      tag.style.top = sy.toFixed(1) + "px";
    }
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
