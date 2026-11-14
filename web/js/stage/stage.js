// The Haze stage: a live 3D view of the rig.
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
import { texture as textureNode, uniform, renderOutput } from "three/tsl";
import { bloom } from "three/addons/tsl/display/BloomNode.js";
import { fxaa } from "three/addons/tsl/display/FXAANode.js";
import { buildFixture } from "./models.js";
import { buildGdtf } from "./gdtf.js";
import { buildVenue, hitDistance, cutaway, releaseScreens, mediaUrlFor } from "./venue.js";
import { skyNode, skyLight, pictureLight } from "./sky.js";
import { Projectors } from "./projectors.js";
import { ndiOffer, ndiOut, ndiSender, ndiSendFrame, ndiOnOut } from "../ndi.js";

/** The room's four sides written on the floor (Arrange, Zones): the DJ /
 *  stage end, the back of the room, left and right as you look at the DJ
 *  from the dance floor - so building a venue, it is plain which end is
 *  which.  They follow where the desk knows the DJ is and which way the DJ
 *  faces (the DJ booth, a DJ zone or the stage, or as set - Room ▸ Which
 *  way round), each written just inside the room's own outline, whatever
 *  its shape (an L-shaped room's labels stay in the room). */
function sideLabels(R, orient, outline) {
  const g = new THREE.Group();
  const label = (text, w, x, z, turn) => {
    const c = document.createElement("canvas");
    c.width = 1024; c.height = 128;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "rgba(47, 214, 245, 0.85)";
    ctx.font = "bold 72px system-ui, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(text, 512, 66);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, w / 8), new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, toneMapped: false }));
    m.rotation.x = -Math.PI / 2;
    m.rotation.z = turn;
    m.position.set(x, 0.03, z);
    m.renderOrder = 3;
    g.add(m);
  };
  const W = Math.max(2, R.x1 - R.x0), D = Math.max(2, R.z1 - R.z0);
  const s = Math.min(W, D) * 0.45;
  const o = orient || {};
  const poly = outline && outline.length >= 3 ? outline : [[R.x0, R.z0], [R.x1, R.z0], [R.x1, R.z1], [R.x0, R.z1]];
  const inside = (x, z) => {
    let c = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, zi] = poly[i], [xj, zj] = poly[j];
      if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) c = !c;
    }
    return c;
  };
  let cx = o.centre ? o.centre[0] : (R.x0 + R.x1) / 2, cz = o.centre ? o.centre[1] : (R.z0 + R.z1) / 2;
  if (!inside(cx, cz)) {
    // an L-shaped room's middle can be outside it: the floor's own middle
    let a = 0, sx = 0, sz = 0;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const k = poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1];
      a += k; sx += (poly[j][0] + poly[i][0]) * k; sz += (poly[j][1] + poly[i][1]) * k;
    }
    if (Math.abs(a) > 1e-6) { cx = sx / (3 * a); cz = sz / (3 * a); }
  }
  // the way the DJ faces (towards the crowd): 0 = +z, as the desk counts it
  const f = ((o.front ?? 0) * Math.PI) / 180;
  const d = [Math.sin(f), Math.cos(f)];
  // from the middle, how far to the wall along u (the outline, else the box)
  const reach = (u) => {
    let best = Infinity;
    for (let i = 0; i < poly.length; i++) {
      const [ax, az] = poly[i], [bx, bz] = poly[(i + 1) % poly.length];
      const ex = bx - ax, ez = bz - az, den = u[0] * ez - u[1] * ex;
      if (Math.abs(den) < 1e-9) continue;
      const t = ((ax - cx) * ez - (az - cz) * ex) / den, k = ((ax - cx) * u[1] - (az - cz) * u[0]) / den;
      if (t > 0.05 && k >= -1e-6 && k <= 1 + 1e-6) best = Math.min(best, t);
    }
    return Number.isFinite(best) ? best : Math.min(W, D) / 2;
  };
  const at = (u, w, text, turn) => {
    const r = Math.max(0.5, reach(u) - w / 16 - 0.4);
    label(text, w, cx + u[0] * r, cz + u[1] * r, turn + f);
  };
  // the DJ's end is behind the DJ (against the way they face); the left as
  // you look at the DJ from the floor is the DJ's right
  if (o.booth && inside(o.booth[0], o.booth[2])) {
    // just in front of the DJ, not on the booth
    const k = Math.min(2.5, Math.hypot(cx - o.booth[0], cz - o.booth[2]) * 0.45);
    label("▲ DJ / STAGE END", s, o.booth[0] + d[0] * k, o.booth[2] + d[1] * k, f);
  } else at([-d[0], -d[1]], s, "▲ DJ / STAGE END", 0);
  at([d[0], d[1]], s, "BACK OF THE ROOM", 0);
  at([-d[1], d[0]], s * 0.9, "LEFT", Math.PI / 2);
  at([d[1], -d[0]], s * 0.9, "RIGHT", -Math.PI / 2);
  g.visible = false;
  return g;
}
import { SfxSystem } from "./sfx.js";
import { Shadows } from "./shadows.js";
import { wireUp, wireTint, WIRE, WIRE_ROOM, WIRE_SEL } from "./wire.js";
import {
  LIGHTS, MAX_BOUNCE, beamGeometry, beamMaterial, glowMap, streakMap, GoboAtlas,
  setDetail,
} from "./materials.js";
import { GPU_DEVICE, rendererOpts } from "./gpu.js";
import { pictureOut, exposureFor } from "./picture.js";
import { VOL, MAX_AREAS, MAX_CLOUDS, volumeGeometry, volumeBeamMaterial, beamUpsample } from "./beams.js";
import { Atmosphere } from "./atmos.js";
import { LightGrid } from "./lightgrid.js";
import { FloorMirror } from "./reflect.js";
import { luxAt } from "./luxmap.js";
import {
  DEG, UP, X_AXIS, DEFAULT_PAN, DEFAULT_TILT, smooth, lerp, hexLinear, EMPTY_LOOK, lookFrom, mixLook, TRAVEL, strobeLevel, turnOf, motorStep,
  hangOf, hangQuat,
} from "./look.js";

export { GPU_DEVICE };
export { FixturePreview } from "./preview.js";

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
      quality: "high", zones: false, dance: true, shadows: true,
      exposure: 0,          // the picture's brightness, in stops (-2 .. +2): the lights don't change
      calm: false,          // calm flashing: strobes shimmer instead of flashing the screen
      bounce: true,         // light bounced off the pools and the screens lights the room
      hazeAuto: false,      // Auto: the haze follows the hazers in the rig (atmos.js)
      lightMap: false };    // the light map: surfaces in false colour by their lux (luxmap.js)
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
    // this view can send the 3D out as NDI (the desktop app picks the window)
    ndiOffer(this, true);
    this._ndiOff = ndiOnOut(() => { this.dirty = true; });
    this.tags = new Map();
    this._c = new THREE.Color();
    this._c2 = new THREE.Color();
    this._v1 = new THREE.Vector3();
    this._v2 = new THREE.Vector3();
    this._m4 = new THREE.Matrix4();
    this._q = new THREE.Quaternion();
    this.gdtfDefs = new Map();
    this.fetchModel = null;
    this.destroyed = false;

    // WebGPU (the graphics card's modern interface); on a computer or
    // browser without it, three.js falls back to WebGL 2 by itself
    let renderer;
    try {
      renderer = new THREE.WebGPURenderer(rendererOpts({ antialias: true, powerPreference: "high-performance" }));
    } catch (e) {
      this.failed = true;
      container.innerHTML = '<div class="stage-fallback">3D view needs WebGPU or WebGL, which this browser has turned off.</div>';
      return;
    }
    this.ready = false;
    this.renderer = renderer;
    this.q.cap = Math.min(window.devicePixelRatio || 1, 1.5);
    this.q.ratio = this.q.cap;
    renderer.setPixelRatio(this.q.ratio);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    // the colour curve is the pipeline's own last step (picture.js: the
    // colour on the desk is the colour on the screen); the exposure is the
    // renderer's, which that step reads
    renderer.toneMapping = THREE.NoToneMapping;
    renderer.toneMappingExposure = exposureFor(0);
    renderer.domElement.className = "stage-canvas";
    renderer.domElement.tabIndex = 0;
    container.appendChild(renderer.domElement);

    this.labels = document.createElement("div");
    this.labels.className = "stage-labels";
    container.appendChild(this.labels);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x050506);
    this.scene = scene;
    this.sfx = new SfxSystem(scene);         // confetti, CO2, flame, fog, lasers
    this.shadows = new Shadows(renderer, scene);   // the crowd and the stage block the beams
    this._shadowRoom = 0;                          // bumped when what casts a shadow changes
    this.grid = new LightGrid();                   // every light on the surfaces (lightgrid.js)
    this.mirror = new FloorMirror();               // a shiny floor mirrors the room (reflect.js)
    this.atmos = new Atmosphere();                 // the haze the hazers make (atmos.js)
    this.hemi = new THREE.HemisphereLight(0xb8c4dc, 0x14151a, 0.22);
    scene.add(this.hemi);
    const key = new THREE.DirectionalLight(0xdfe7ff, 0.35);
    key.position.set(4, 9, 12);
    scene.add(key);
    this.key = key;

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.05, 400);
    this.camera.position.set(0, 4, 18);
    // the flat views (Top / Front / Side / Free) look through this one
    this.persp = this.camera;
    this.ortho = new THREE.OrthographicCamera(-5, 5, 5, -5, 0.1, 1500);
    this.orthoHalf = 5;                         // half the height it shows, metres
    this.proj = "per";                           // per | top | front | side | free
    this.mouseMode = "camera";                   // camera | fixtures | elements
    this.wire = false;
    this.fps = 0;
    this._fpsN = 0;
    this._fpsT = performance.now();
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

    // the picture: the scene, plus a glow round the bright parts (bloom);
    // tone mapping and colour space happen at the end of the pipeline
    this.post = new THREE.RenderPipeline(renderer);
    this._makePipeline(false);
    // the renderer starts asynchronously (it asks the graphics card first)
    renderer.init().then(() => {
      if (this.destroyed) return;
      const pmrem = new THREE.PMREMGenerator(renderer);
      scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
      this.backend = renderer.backend && renderer.backend.isWebGPUBackend ? "webgpu" : "webgl2";
      this.ready = true;
      this.dirty = true;
    }).catch((e) => {
      console.error(e);
      this.failed = true;
      container.insertAdjacentHTML("beforeend", '<div class="stage-fallback">3D view: this computer\'s graphics could not start.</div>');
    });

    this.venueGroup = new THREE.Group();
    scene.add(this.venueGroup);
    this.rigGroup = new THREE.Group();
    scene.add(this.rigGroup);
    this.beamGroup = new THREE.Group();
    scene.add(this.beamGroup);
    // the beams in haze (Medium and High): their own scene, drawn after the
    // room into a half-size picture of their own (beams.js)
    this.beamScene = new THREE.Scene();
    this.projectors = new Projectors(this.beamScene);   // video thrown onto the room (projectors.js)

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
    this.persp.aspect = w / h;
    this.persp.updateProjectionMatrix();
    this._orthoFrustum();
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
    releaseScreens(this.built);
    this.venueGroup.clear();
    const built = buildVenue(auto ? null : venue, fixtures, {
      people: this.options.people, zones: this.options.zones,
      loadUnderlay: this.opts.loadUnderlay, onChange: () => { this.dirty = true; } });
    this.built = built;
    this.projectors.setRoom(built);
    this._shadowRoom++;
    this.venueGroup.add(built.group);
    this._wireDirty = true;
    this.room = { ...built.room };
    this.stageFront = built.stageFront;
    // which end is which, written on the floor while building the room
    this._outline = venue && venue.room && venue.room.outline && venue.room.outline.length >= 3 ? venue.room.outline : null;
    this._sidesSig = "";
    this.setSides(this.djView);
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
    // where the haze is thicker (the venue's haze areas: beams.js VOL)
    const areas = (built.venue.objects || []).filter((o) => o.kind === "haze").slice(0, MAX_AREAS);
    areas.forEach((o, i) => {
      VOL.areaC.value[i].set(o.x, (o.y || 0) + o.h / 2, o.z, o.density ?? 0.6);
      VOL.areaH.value[i].set(o.w / 2, o.h / 2, o.d / 2, (o.rot || 0) * DEG);
    });
    VOL.areaCount.value = areas.length;
    this.segments = built.segments;
    this.people = built.people;
    if (this.options.lightMap) this._applyLightMap();
    this._applySky();
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
    this._wireDirty = true;
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
    for (const b of inst.beams) this._dropBeam(b);
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
      const streak = new THREE.Sprite(new THREE.SpriteMaterial({
        map: streakMap(), blending: THREE.AdditiveBlending, depthWrite: false,
        transparent: true, toneMapped: false,
      }));
      streak.visible = false;
      streak.renderOrder = 3;
      this.beamGroup.add(streak);
      // the same beam as light in the air (Medium and High)
      const vol = new THREE.Mesh(volumeGeometry(), volumeBeamMaterial());
      vol.frustumCulled = false;
      vol.visible = false;
      this.beamScene.add(vol);
      return { em, mesh, mat, glow, streak, vol, shadowSlot: -1, origin: new THREE.Vector3(), dir: new THREE.Vector3() };
    });
  }

  _dropBeam(b) {
    this.beamGroup.remove(b.mesh, b.glow, b.streak);
    b.mat.dispose();
    b.glow.material.dispose();
    b.streak.material.dispose();
    if (b.vol) {
      this.beamScene.remove(b.vol);
      b.vol.material.dispose();
    }
  }

  _remove(inst) {
    this.rigGroup.remove(inst.holder);
    for (const b of inst.beams) this._dropBeam(b);
  }

  /** Hang or stand the model, the right way up, where the patch says. */
  _place(inst) {
    const f = inst.data;
    // how it is mounted: hung, standing, on its back, on a boom (the desk
    // aims with the same table, so the 3D beam is where the real one goes)
    const hang = hangOf(f);
    inst.hang = hang;
    inst.hung = hang === "hung";
    inst.holder.position.set(+f.x || 0, +f.y || 0, +f.z || 0);
    // which way its base faces (a turned truss turns its lights)
    inst.holder.rotation.set(0, (+f.yaw || 0) * DEG, 0);
    hangQuat(hang, inst.sk.native === "hung", inst.sk.root.quaternion);
    // the click volume follows the body, whichever way it points
    hangQuat(hang, false, inst.pick.quaternion);
    inst.pick.position.set(0, inst.sk.height / 2, 0).applyQuaternion(inst.pick.quaternion);
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
    if (inst.hang && inst.hang !== "hung" && inst.hang !== "standing") {
      // on its back or on a boom: it shines straight out, the way its head points
      this._setStaticDir(inst, new THREE.Vector3(0, 1, 0).applyQuaternion(hangQuat(inst.hang))
        .applyAxisAngle(UP, (+f.yaw || 0) * DEG));
      return;
    }
    let target;
    if (body.type === "tube") {
      target = new THREE.Vector3(p.x * 1.2, p.y, p.z + 6);
    } else if (body.type === "followspot") {
      target = new THREE.Vector3(0, 1.4, front * 0.6);
    } else if (body.type === "blinder") {
      target = new THREE.Vector3(p.x, 1.6, p.z + 8);
    } else if (body.type === "laser") {
      // a laser shoots over the crowd: hung, down onto the floor in front;
      // standing (floor, booth, on a truss), out and a little up over heads
      target = inst.hung ? new THREE.Vector3(p.x * 0.8, 0, Math.max(p.z, front) + 4)
        : new THREE.Vector3(p.x * 0.8, Math.max(p.y + 0.6, 2.6), Math.max(p.z, front) + 10);
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
            this._wireDirty = true;
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
    const next = new Set((list || []).map(Number));
    // the desk's quick updates send the selection about ten times a second:
    // the same one again draws nothing new, so it doesn't redraw (an idle 3D
    // otherwise never rests, which a machine with no graphics card can't keep up with)
    if (this.selected && next.size === this.selected.size && [...next].every((id) => this.selected.has(id))) return;
    this.selected = next;
    this.dirty = true;
  }

  /** Lights to point out without selecting them (a group's folder under
   *  the pointer in the fixture list): an amber ring and their numbers. */
  setPeek(list) {
    this.peek = new Set((list || []).map(Number));
    this.dirty = true;
  }

  setOptions(o) {
    const before = { ...this.options };
    Object.assign(this.options, o || {});
    if (this.people) this.people.visible = !!this.options.people;
    if (before.people !== this.options.people) this._shadowRoom++;
    if (this.built && this.built.zones) this.built.zones.visible = !!this.options.zones;
    if (this.sideLabels) this.sideLabels.visible = !!this.editing || !!this.options.zones;
    // Lights only: the room, rigging, stage, objects and crowd hidden - the lights and their beams stay
    this.venueGroup.visible = !this.options.lightsOnly;
    if (before.house !== this.options.house) this._applyHouse();
    if (before.exposure !== this.options.exposure) this._applyExposure();
    if (before.hazeAuto !== this.options.hazeAuto) this.atmos.clear();      // Auto starts from clear air
    if (!!before.lightMap !== !!this.options.lightMap) this._applyLightMap();
    if (before.quality !== this.options.quality) {
      const dpr = window.devicePixelRatio || 1;
      this.q.cap = this.options.quality === "high" ? Math.min(dpr, 2)
        : this.options.quality === "fast" ? Math.min(dpr, 0.75) : Math.min(dpr, 1.5);
      this.q.ratio = this.q.cap;
      this.resize();
      setDetail(this.scene, this.options.quality !== "fast");
      if (this.post && (this.options.quality === "high") !== this._smooth) this._makePipeline(this.options.quality === "high");
    }
    this.dirty = true;
  }

  /** The light map on or off: the surfaces' shader switches to false
   *  colour, the people's own colours go white (the map is their light, not
   *  their clothes) and the beams in the air go (beams.js, _updateBeams). */
  _applyLightMap() {
    const on = !!this.options.lightMap;
    LIGHTS.uLuxMap.value = on ? 1 : 0;
    // (the crowd and the performers on their marks)
    if (this.built && this.built.people) {
      this.built.people.traverse((m) => {
        if (!m.isInstancedMesh || !m.instanceColor) return;
        const arr = m.instanceColor.array;
        if (on && !m.userData.ownColours) {
          m.userData.ownColours = arr.slice();
          arr.fill(1);
        } else if (!on && m.userData.ownColours) {
          arr.set(m.userData.ownColours);
          m.userData.ownColours = null;
        } else return;
        m.instanceColor.needsUpdate = true;
      });
    }
    this.dirty = true;
  }

  /**
   * The light meter: the lux the rig puts on point `p` (a surface facing
   * `n`), worked out as the light map's shader does (luxmap.js) - the
   * lights as they are now, the distance, the angle, the beam's edge and
   * gobo, and anything solid in the way.  With `why`: {lux, by: Map(head ->
   * lux)}, each light's share.
   */
  luxAt(p, n = UP, why = false) {
    const blocked = (o, d, dist) => hitDistance(this.planes, this.boxes, o, d, dist + 0.2, this.segments);
    const atlas = this.gobos ? { canvas: this.gobos.canvas, version: this.gobos.version } : null;
    return luxAt(this._lightList || [], p, n, { blocked, atlas, why });
  }

  /** What's under the pointer: the surface's point and which way it faces
   *  (the floor, a wall, the stage, an object, a person), or null. */
  pointAt(ev) {
    if (!this.built) return null;
    const r = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const targets = [];
    // (seen: it and everything it hangs in shown - the crowd's group hides it)
    this.built.group.traverseVisible((o) => {
      if (!o.isMesh || o.userData.zone) return;
      if (o.userData.venueKind === "underlay" || o.userData.editOnly) return;
      targets.push(o);
    });
    const hit = this.raycaster.intersectObjects(targets, false)[0];
    if (!hit) return null;
    const n = hit.face ? hit.face.normal.clone().transformDirection(hit.object.matrixWorld) : UP.clone();
    if (hit.object.isInstancedMesh && hit.instanceId !== undefined && hit.face) {
      const m = new THREE.Matrix4();
      hit.object.getMatrixAt(hit.instanceId, m);
      n.copy(hit.face.normal).transformDirection(m).transformDirection(hit.object.matrixWorld);
    }
    // the side the camera sees (a plane seen from behind)
    if (n.dot(this.raycaster.ray.direction) > 0) n.negate();
    return { point: hit.point.clone(), normal: n.normalize(),
      kind: hit.object.userData.crowd ? "person" : hit.object.userData.performer ? "performer" : hit.object.userData.venueKind || "object" };
  }

  /**
   * Light readings for the AI and the light map (measure_light): `spots`
   * [{name, x, y, z, facing}] - facing "up" (the floor's light: what lands
   * on it), "audience" (a face on stage, towards the room) or "stage" (a
   * face in the room, towards the stage) - or, with none, the room's own
   * places: the stage, a face on it, and each zone at its middle.
   */
  luxReport(spots = null) {
    this._freshLights();
    const v = (this.built && this.built.venue) || {};
    const list = [];
    const front = this.stageFront || 0;
    const toward = (facing) => (facing === "audience" ? new THREE.Vector3(0, 0, 1)
      : facing === "stage" ? new THREE.Vector3(0, 0, -1) : UP.clone());
    if (Array.isArray(spots) && spots.length) {
      for (const s of spots.slice(0, 24)) {
        const x = +s.x || 0, z = +s.z || 0;
        const y = s.y === undefined || s.y === null ? this._groundAt(x, z) : +s.y;
        list.push({ name: String(s.name || `${x.toFixed(1)}, ${z.toFixed(1)}`).slice(0, 40), p: new THREE.Vector3(x, y, z), facing: s.facing || "up" });
      }
    } else {
      const st = v.stage;
      if (st) {
        const y = Math.max(0, st.height || 0) + 0.01;
        list.push({ name: "the stage (its floor, middle)", p: new THREE.Vector3(st.x, y, st.z + st.depth / 2), facing: "up" });
        list.push({ name: "a face on the stage (1.6 m up, towards the audience)", p: new THREE.Vector3(st.x, y + 1.6, st.z + st.depth * 0.6), facing: "audience" });
      }
      for (const zn of (v.zones || []).slice(0, 16)) {
        if (!zn.points || zn.points.length < 3) continue;
        const x = zn.points.reduce((a, q) => a + q[0], 0) / zn.points.length;
        const z = zn.points.reduce((a, q) => a + q[1], 0) / zn.points.length;
        const nm = zn.name || zn.kind || "zone";
        list.push({ name: `${nm} (floor, middle)`, p: new THREE.Vector3(x, this._groundAt(x, z), z), facing: "up" });
        if (zn.kind === "dj") list.push({ name: `${nm}: the DJ's face (1.6 m up)`, p: new THREE.Vector3(x, this._groundAt(x, z) + 1.6, z), facing: z > front ? "stage" : "audience" });
      }
      if (!list.length) list.push({ name: "the middle of the room (floor)", p: new THREE.Vector3(this.room.cx || 0, 0.01, (this.room.z0 + this.room.z1) / 2), facing: "up" });
    }
    return list.map(({ name, p, facing }) => {
      const r = this.luxAt(p, toward(facing), true);
      const top = [...r.by.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4)
        .map(([head, lux]) => ({ light: head, lux: Math.round(lux) }));
      return { name, at: [+p.x.toFixed(2), +p.y.toFixed(2), +p.z.toFixed(2)], facing, lux: Math.round(r.lux), from: top };
    });
  }

  /** Are the lights where the desk sent them (no fade or move still going)? */
  lightsSettled() {
    for (const inst of this.fixtures.values()) if (!inst.settled || inst.moving) return false;
    return true;
  }

  /** A reading needs the lights as they are: when the 3D isn't drawing
   *  (its window hidden, a workspace without it), work them out once now,
   *  fades and moves at their end. */
  _freshLights() {
    const now = performance.now();
    if (now - (this._lastDraw || 0) < 300) return;
    const time = (now - this.t0) / 1000;
    const lights = [];
    for (const inst of this.fixtures.values()) {
      if (!inst.settled) { inst.cur = mixLook(inst.cur, inst.to, 1); inst.settled = true; inst.mixAt = now; }
      const m = inst.motor;
      if (m) {
        if (inst.cur.pan !== null && inst.cur.pan !== undefined) { m.pan.x = inst.cur.pan; m.pan.v = 0; }
        if (inst.cur.tilt !== null && inst.cur.tilt !== undefined) { m.tilt.x = inst.cur.tilt; m.tilt.v = 0; }
      }
      this._drive(inst, time, lights);
    }
    this.rigGroup.updateMatrixWorld(true);
    this._wireSegs = null;
    for (const inst of this.fixtures.values()) this._updateBeams(inst, time, lights);
    this._projectorLights(lights);
    this._uploadLights(lights);
  }

  /** The projectors as lights, with the rig's (projectors.js). */
  _projectorLights(lights) {
    this.projectors.update(lights, {
      hit: (o, d, cap) => hitDistance(this.planes, this.boxes, o, d, cap, this.segments),
      haze: this._hazeNow(), volumetric: this._volumetric(), lightMap: !!this.options.lightMap,
    });
  }

  /** The floor's height at (x, z): the stage deck there, else the floor. */
  _groundAt(x, z) {
    const st = this.built && this.built.venue && this.built.venue.stage;
    if (st && Math.abs(x - st.x) <= st.width / 2 && z >= st.z && z <= st.z + st.depth) return Math.max(0, st.height || 0) + 0.01;
    return 0.01;
  }

  /** The sky over a room with no roof (sky.js): drawn behind everything,
   *  and the light it gives the room (_applyHouse).  Not in the line
   *  drawings.  A picture shows the night sky until it has loaded. */
  _applySky() {
    const room = (this.built && this.built.venue && this.built.venue.room) || {};
    const outdoor = room.ceiling === "none" && !this.wire;
    const kind = outdoor ? String(room.sky || "night") : "";
    const turn = +room.sky_turn || 0;
    const key = outdoor ? `${kind}|${turn}` : "";
    if (key === this._skyKey) return;
    this._skyKey = key;
    this.skyKind = kind;
    if (!outdoor) {
      this.scene.backgroundNode = null;
      this.skyLight = null;
      this._applyHouse();
      return;
    }
    const pic = kind.startsWith("picture:") ? kind.slice(8) : "";
    // the ground beyond the field: grass, or a darker ground
    const ground = room.floor === "grass" ? [0.025, 0.041, 0.018] : [0.02, 0.022, 0.02];
    const s = skyNode(pic ? "night" : kind, turn, null, ground);
    this.scene.backgroundNode = s.node;
    this.skyLight = skyLight(pic ? "night" : kind);
    if (pic) {
      mediaUrlFor(pic).then((url) => new Promise((ok, bad) => {
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => ok(img);
        img.onerror = bad;
        img.src = url;
      })).then((img) => {
        if (this._skyKey !== key || this.destroyed) return;
        const tex = new THREE.Texture(img);
        tex.colorSpace = THREE.SRGBColorSpace;
        tex.needsUpdate = true;
        this.skyLight = pictureLight(img);
        tex.userData.ev = this.skyLight.ev;
        this.scene.backgroundNode = skyNode("picture", turn, tex).node;
        this._applyHouse();
      }).catch(() => { /* the night sky stays */ });
    }
    this._applyHouse();
  }

  /** The picture's exposure: the slider's, plus what a bright sky takes
   *  (sky.js: fixed for each sky - the lights never change it). */
  _applyExposure() {
    if (!this.renderer) return;
    const ev = (this.skyLight && this.skyLight.ev) || 0;
    this.renderer.toneMappingExposure = exposureFor((+this.options.exposure || 0) + ev);
    (this._bloomScale || (this._bloomScale = uniform(1))).value = Math.pow(2, ev);
  }

  /** House lights: how much of the room you see with the rig dark (and,
   *  outdoors, the sky's light on it). */
  _applyHouse() {
    const k = Math.max(0, Math.min(1, this.options.house));
    LIGHTS.uAmbient.value.setRGB(0.55, 0.55, 0.58).multiplyScalar(0.03 + Math.pow(k, 1.1) * 1.9);
    const sky = this.skyLight;
    if (sky) {
      LIGHTS.uAmbient.value.r += sky.light[0];
      LIGHTS.uAmbient.value.g += sky.light[1];
      LIGHTS.uAmbient.value.b += sky.light[2];
    }
    this.hemi.intensity = 0.12 + k * 1.5 + (sky ? sky.room : 0);
    this.key.intensity = 0.15 + k * 1.1 + (sky ? sky.room * 1.2 : 0);
    this._applyExposure();
    this.scene.background.setRGB(0.014, 0.014, 0.016).lerp(new THREE.Color(0.055, 0.055, 0.06), k);
    if (this.wire) this.scene.background.setRGB(0, 0, 0);          // a line drawing: on black
    this.dirty = true;
  }

  // ---------------------------------------------------------------- camera
  cameraState() {
    return { pos: this.camera.position.toArray(), target: this.controls.target.toArray() };
  }

  setCamera(s) {
    if (!s || !s.pos || !s.target) return;
    this._perspective();
    this.povHead = null;
    // a view picked while the camera is still flying somewhere (a room
    // that just changed size flies to its front) is the one that stays
    this.fly = null;
    this.camera.position.fromArray(s.pos);
    this.controls.target.fromArray(s.target);
    this.controls.update();
    this.dirty = true;
  }

  view(name, instant = false) {
    this._perspective();
    if (name === "dj") return this.viewStage();
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
    this._perspective();
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

  /** From the DJ booth (where the operator set it, else the DJ mark or the
   *  stage centre), looking the way the DJ faces, at the middle of the room. */
  viewStage() {
    this._perspective();
    const o = this.djView;
    if (o && o.booth && o.centre) {
      const [x, y, z] = o.booth, a = (o.front || 0) * DEG;
      const dist = Math.max(3, Math.hypot(o.centre[0] - x, o.centre[1] - z));
      this._flyTo(new THREE.Vector3(x, y, z), new THREE.Vector3(x + Math.sin(a) * dist, 1.2, z + Math.cos(a) * dist), false);
      return;
    }
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
    this._perspective();
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
    if (this.camera.isOrthographicCamera) {
      this._orthoTo(sphere.center, dir, sphere.radius * 2.1, sphere.radius * 2.1);
      return;
    }
    const dist = sphere.radius / Math.sin((this.camera.fov * DEG) / 2) * 1.1;
    this._flyTo(sphere.center.clone().add(dir.multiplyScalar(Math.max(dist, 3))), sphere.center, false);
  }

  zoom(factor) {
    if (this.camera.isOrthographicCamera) {
      this.camera.zoom = Math.max(0.05, Math.min(60, this.camera.zoom * factor));
      this.camera.updateProjectionMatrix();
      this.dirty = true;
      return;
    }
    const dir = this.camera.position.clone().sub(this.controls.target);
    dir.multiplyScalar(1 / Math.max(0.2, factor));
    this._flyTo(this.controls.target.clone().add(dir), this.controls.target.clone(), false);
  }

  // ------------------------------------------------- the visualiser's views
  /** Per (perspective, shaded and lit), or a flat view drawn as lines,
   *  white on black: Top / Front / Side (framing the room) or Free (turned
   *  any way you like).  Returns the projection now in use. */
  /** The floor's side labels for where the DJ is (the desk's venue_orient). */
  setSides(orient) {
    if (!this.room || !this.venueGroup) return;
    const sig = JSON.stringify([orient && orient.centre, orient && orient.front, this._outline, this.room.x0, this.room.x1, this.room.z0, this.room.z1]);
    if (sig === this._sidesSig && this.sideLabels) return;
    this._sidesSig = sig;
    if (this.sideLabels) {
      this.sideLabels.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); } });
      this.venueGroup.remove(this.sideLabels);
    }
    this.sideLabels = sideLabels(this.room, orient, this._outline);
    this.sideLabels.visible = !!this.editing || !!this.options.zones;
    this.venueGroup.add(this.sideLabels);
    this.dirty = true;
  }

  setProjection(kind) {
    kind = ["per", "top", "front", "side", "free"].includes(kind) ? kind : "per";
    if (kind !== "per" && this.walking) this.walk(false);
    const was = this.proj;
    this.proj = kind;
    this.fly = null;
    this.povHead = null;
    if (kind === "per") {
      this._useCamera(this.persp);
      if (was !== "per" && this._persKeep) {
        this.persp.position.copy(this._persKeep.pos);
        this.controls.target.copy(this._persKeep.target);
      }
      this._persKeep = null;
    } else {
      if (was === "per") this._persKeep = { pos: this.persp.position.clone(), target: this.controls.target.clone() };
      this._useCamera(this.ortho);
    }
    this.controls.enableRotate = kind === "per" || kind === "free";
    this.controls.maxPolarAngle = kind === "per" ? Math.PI * 0.495 : Math.PI;
    if (kind !== "per") this.fitRoom(true);
    this._setWire(kind !== "per");                 // every flat view is a line drawing
    this._applyMouse();
    this.controls.update();
    this.camMoved = true;
    this.dirty = true;
    if (this.opts.onProjection) this.opts.onProjection(kind);
    return kind;
  }

  /** Back to the perspective view (the named views, walking, a light's
   *  beam are all seen in perspective). */
  _perspective() {
    if (this.proj !== "per") {
      this._persKeep = null;
      this.setProjection("per");
    }
  }

  _useCamera(cam) {
    if (this.camera === cam) return;
    this.camera = cam;
    this.controls.object = cam;
    this.resize();
  }

  _orthoFrustum() {
    const w = Math.max(1, this.el.clientWidth), h = Math.max(1, this.el.clientHeight);
    const a = w / h, hh = this.orthoHalf || 5;
    Object.assign(this.ortho, { left: -hh * a, right: hh * a, top: hh, bottom: -hh });
    this.ortho.updateProjectionMatrix();
  }

  /** Point the flat camera at `centre` from `dir`, showing `w` x `h` m. */
  _orthoTo(centre, dir, w, h) {
    const W = Math.max(1, this.el.clientWidth), H = Math.max(1, this.el.clientHeight);
    this.orthoHalf = Math.max(0.5, Math.max(h / 2, (w / 2) * (H / W)));
    this.ortho.zoom = 1;
    this.ortho.position.copy(centre).addScaledVector(dir.clone().normalize(), 400);
    this.ortho.up.set(0, 1, 0);
    this.controls.target.copy(centre);
    this._orthoFrustum();
    this.controls.update();
    this.camMoved = true;
    this.dirty = true;
  }

  /** Everything there is to see: the room, its rigging and the lights. */
  roomBox() {
    const R = this.room;
    const box = new THREE.Box3(new THREE.Vector3(R.x0, 0, R.z0), new THREE.Vector3(R.x1, R.h, R.z1));
    for (const inst of this.fixtures.values()) box.expandByPoint(inst.holder.position);
    return box;
  }

  /** "Fit to whole room": the whole room in the view, from where it looks now. */
  fitRoom(instant = false) {
    const box = this.roomBox();
    const c = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
    const m = 1.08;
    if (this.camera.isOrthographicCamera) {
      const k = this.proj;
      if (k === "top") this._orthoTo(new THREE.Vector3(c.x, 0, c.z), new THREE.Vector3(0, 1, 0.0005), size.x * m, size.z * m);
      else if (k === "front") this._orthoTo(c, new THREE.Vector3(0, 0, 1), size.x * m, size.y * m);
      else if (k === "side") this._orthoTo(c, new THREE.Vector3(1, 0, 0), size.z * m, size.y * m);
      else {
        // free: from where it looks now (the first time: from the front,
        // up and to one side)
        const dir = this._freeSet && !instant
          ? this.ortho.position.clone().sub(this.controls.target) : new THREE.Vector3(0.75, 0.6, 1);
        this._freeSet = true;
        const r = size.length() / 2;
        this._orthoTo(c, dir, r * 2 * m, r * 1.6 * m);
      }
      return;
    }
    // back off along the way it looks until every corner of the room is in
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    const right = new THREE.Vector3().crossVectors(UP, dir);
    if (right.lengthSq() < 1e-6) right.set(1, 0, 0);
    right.normalize();
    const up = new THREE.Vector3().crossVectors(dir, right);
    const tanV = Math.tan((this.camera.fov * DEG) / 2) * 0.94, tanH = tanV * (this.camera.aspect || 1);
    let dist = 1;
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
      const o = new THREE.Vector3(x, y, z).sub(c);
      const depth = o.dot(dir);
      dist = Math.max(dist, depth + Math.abs(o.dot(right)) / tanH, depth + Math.abs(o.dot(up)) / tanV);
    }
    this._flyTo(c.clone().addScaledVector(dir, dist), c, instant);
  }

  /** What a left-drag does: turn the view (Move Camera), or draw a box
   *  round lights (Select Fixtures / Select Elements). */
  setMouseMode(mode) {
    this.mouseMode = ["camera", "fixtures", "elements"].includes(mode) ? mode : "camera";
    this._applyMouse();
    return this.mouseMode;
  }

  _applyMouse() {
    const turn = this.controls.enableRotate ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    const sel = this.mouseMode !== "camera";
    this.controls.mouseButtons = {
      LEFT: sel ? null : turn,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: sel ? turn : THREE.MOUSE.PAN,
    };
    if (this.renderer) this.renderer.domElement.classList.toggle("box-select", sel);
  }

  _setWire(on) {
    this.wire = !!on;
    this._wireDirty = true;
    this.el.classList.toggle("wire", this.wire);
    this.beamGroup.visible = !this.wire;
    if (!this.wire) {
      wireUp(this.venueGroup, false);
      wireUp(this.rigGroup, false);
      if (this._wireBeams) this._wireBeams.visible = false;
      this._applySky();
      this._applyHouse();
    } else {
      this._applySky();
      this.scene.background.setRGB(0, 0, 0);
    }
    this.dirty = true;
  }

  /** The line drawing for parts built since (a new light, a new truss). */
  _wireUpkeep() {
    if (!this._wireDirty) return;
    this._wireDirty = false;
    if (!this.wire) return;
    wireUp(this.venueGroup, true, WIRE_ROOM);
    // rigging and objects in white, the room itself in grey
    if (this.built && this.built.items) for (const g of this.built.items.values()) wireTint(g, WIRE);
    for (const inst of this.fixtures.values()) {
      if (inst.ring) inst.ring.userData.wireSkip = true;
      inst.wireSel = this.selected.has(inst.head);
      wireUp(inst.holder, true, inst.wireSel ? WIRE_SEL : WIRE);
    }
  }

  /** Lit beams in a line drawing: one thin line each, in its colour. */
  _wireBeamLines(segs) {
    if (!this._wireBeams) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6 * 512), 3));
      geo.setAttribute("color", new THREE.BufferAttribute(new Float32Array(6 * 512), 3));
      this._wireBeams = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ vertexColors: true, toneMapped: false }));
      this._wireBeams.frustumCulled = false;
      this.scene.add(this._wireBeams);
    }
    const geo = this._wireBeams.geometry;
    const pos = geo.attributes.position.array, col = geo.attributes.color.array;
    const n = Math.min(512, segs.length);
    for (let i = 0; i < n; i++) {
      const [o, d, len, r, g, b] = segs[i];
      pos.set([o.x, o.y, o.z, o.x + d.x * len, o.y + d.y * len, o.z + d.z * len], i * 6);
      col.set([r, g, b, r * 0.3, g * 0.3, b * 0.3], i * 6);
    }
    geo.setDrawRange(0, n * 2);
    geo.attributes.position.needsUpdate = true;
    geo.attributes.color.needsUpdate = true;
    this._wireBeams.visible = n > 0;
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

  /** The head (1, 2...) of a multi-head light nearest a point on screen,
   *  or 0 for a light with one head. */
  cellAt(inst, cx, cy) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.rigGroup.updateMatrixWorld(true);
    const v = this._v1;
    const dist = () => {
      v.project(this.camera);
      return Math.hypot(r.left + (v.x + 1) / 2 * r.width - cx, r.top + (1 - v.y) / 2 * r.height - cy);
    };
    let best = 0, bestD = Infinity;
    // a bar's pixels (each cell drawn as a few of them)
    const n = (inst.data.body && inst.data.body.cells) || (inst.cur && inst.cur.cells ? inst.cur.cells.length : 0);
    const px = inst.sk.pixels;
    if (px && n > 1) {
      for (let i = 0; i < px.count; i++) {
        px.getMatrixAt(i, this._m4);
        v.set(0, 0, 0).applyMatrix4(this._m4).applyMatrix4(px.matrixWorld);
        const d = dist();
        if (d < bestD) { bestD = d; best = Math.floor(i * n / px.count) + 1; }
      }
      return best;
    }
    // a multi-head light's heads (each its own beam)
    const cells = inst.beams.filter((b) => b.em.cell !== undefined);
    if (cells.length < 2) return 0;
    for (const b of cells) {
      b.em.node.getWorldPosition(v);
      const d = dist();
      if (d < bestD) { bestD = d; best = b.em.cell + 1; }
    }
    return best;
  }

  /** Mark the heads picked with Select Elements (an accent ring on each). */
  setCells(head, cells) {
    this.cellPick = head && cells && cells.length ? { head: Number(head), cells: cells.map(Number) } : null;
    this.dirty = true;
  }

  /** Where head `k` (1, 2...) of a light is, in the room. */
  _cellPoint(inst, k, out) {
    const n = (inst.data.body && inst.data.body.cells) || (inst.cur && inst.cur.cells ? inst.cur.cells.length : 0);
    const px = inst.sk.pixels;
    if (px && n > 1) {
      out.set(0, 0, 0);
      let c = 0;
      for (let i = 0; i < px.count; i++) {
        if (Math.floor(i * n / px.count) + 1 !== k) continue;
        px.getMatrixAt(i, this._m4);
        out.add(this._v2.set(0, 0, 0).applyMatrix4(this._m4).applyMatrix4(px.matrixWorld));
        c++;
      }
      return c ? out.multiplyScalar(1 / c) : null;
    }
    const b = inst.beams.find((x) => x.em.cell === k - 1);
    return b ? b.em.node.getWorldPosition(out) : null;
  }

  _drawCellMarks() {
    const pick = this.cellPick;
    const inst = pick && this.selected.has(pick.head) ? this.fixtures.get(pick.head) : null;
    const want = inst ? pick.cells : [];
    if (!this._cellMarks) this._cellMarks = [];
    while (this._cellMarks.length < want.length) {
      const m = new THREE.Mesh(new THREE.RingGeometry(0.05, 0.075, 24), new THREE.MeshBasicMaterial({
        color: 0x2fd6f5, toneMapped: false, depthTest: false, transparent: true, side: THREE.DoubleSide }));
      m.renderOrder = 998;
      this.scene.add(m);
      this._cellMarks.push(m);
    }
    this._cellMarks.forEach((m, i) => {
      const p = i < want.length ? this._cellPoint(inst, want[i], m.position) : null;
      m.visible = !!p;
      if (p) m.lookAt(this.camera.position);
    });
  }

  _wirePointer() {
    const dom = this.renderer.domElement;
    let down = null;
    // Shift-drag draws a box: every light inside it is selected (the
    // orbit waits).  Captured first, so the orbit never starts.
    let box = null;
    // (Select Fixtures / Select Elements: a plain drag draws the box)
    dom.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0 || this.editing || this.pickOnce || !this.opts.onBox || this.walking || this._follow) return;
      if (!ev.shiftKey && this.mouseMode === "camera") return;
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
      const sel = this.mouseMode !== "camera";
      this.opts.onBox(this.headsInRect(b.x, b.y, ev.clientX, ev.clientY),
        { add: ev.ctrlKey || ev.metaKey || (sel && ev.shiftKey) });
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
      if (!inst) return;
      // Select Elements: the head (cell) of a multi-head light under the pointer
      const cell = this.mouseMode === "elements" ? this.cellAt(inst, ev.clientX, ev.clientY) : 0;
      if (cell && this.opts.onElement) {
        this.opts.onElement(inst.head, cell, { add: ev.shiftKey || ev.ctrlKey || ev.metaKey });
      } else if (this.opts.onPick) {
        this.opts.onPick(inst.head, this.mouseMode === "camera"
          ? { shift: ev.shiftKey, toggle: ev.ctrlKey || ev.metaKey }
          : { add: ev.shiftKey, toggle: ev.ctrlKey || ev.metaKey });
      }
    };
    dom.addEventListener("pointerup", up);
    dom.addEventListener("pointercancel", () => { down = null; });
    dom.addEventListener("contextmenu", (ev) => {
      const inst = this.editing ? null : this._hit(ev);
      if (inst && this.opts.onMenu) { ev.preventDefault(); this.opts.onMenu(inst.head, ev); }
    });
    dom.addEventListener("dblclick", (ev) => {
      if (this.editing && this.editor && this.editor.drawing) { this.editor.finishDraw(); return; }
      const inst = this._hit(ev);
      if (inst) this.frame([inst.head]);
    });
  }

  /** The traced floor plan is a drawing aid: shown while arranging. */
  _underlayVisibility() {
    // (a haze area's dashed box too: shown while arranging)
    if (this.built) this.built.group.traverse((o) => { if (o.userData && o.userData.editOnly) o.visible = !!this.editing; });
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
    if (this.sideLabels) this.sideLabels.visible = this.editing || !!this.options.zones;
    if (this.editing) this.setProjection("per");
    if (this.editor) this.editor.setEnabled(this.editing);
    this._underlayVisibility();
    this.dirty = true;
  }

  _wireKeys() {
    const dom = this.renderer.domElement;
    const views = { 1: "front", 2: "left", 3: "right", 4: "back", 5: "top", 6: "overview", 7: "dj" };
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
    // hidden: the window, or a workspace with no 3D view (nothing to draw)
    if (document.hidden || document.body.classList.contains("ws-nostage")) return;
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
    // a strobe draws every picture the screen can show: at 30 a second a
    // fast one ran at the wrong speed
    if (this.strobing) motion = true;
    if (this._hazeBusy) motion = true;           // a hazer's cloud rolling out
    // A lit rig still animates (strobe, haze drift, the crowd), but 30
    // frames a second is plenty for that; a moving one gets every frame.
    if (!this.ready) return;
    // recording a video: a steady 30 frames a second, lit or not
    if (!this.dirty && !motion && !((this.anyLit || this.recording) && now - this.lastRender > 32)) return;

    const time = (now - this.t0) / 1000;
    LIGHTS.uTime.value = time;
    LIGHTS.uBounce.value = this.options.dance ? 1 : 0;
    const lights = [];
    let lit = 0;
    this._strobeNow = false;
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
    this.strobing = this._strobeNow;
    this._wireUpkeep();
    this.rigGroup.updateMatrixWorld(true);
    this._wireSegs = this.wire ? [] : null;
    for (const inst of this.fixtures.values()) this._updateBeams(inst, time, lights);
    if (this.wire) this._wireBeamLines(this._wireSegs);
    const dtS = this._sfxLast ? Math.min(0.1, (now - this._sfxLast) / 1000) : 0;
    this._sfxLast = now;
    this._stepHaze(dtS);
    this.sfxBusy = this.sfx.update(dtS, time, this.fixtures,
      (o, d) => hitDistance(this.planes, this.boxes, o, d, 30, this.segments));
    this._projectorLights(lights);
    this._uploadLights(lights);
    // a beam whose light has a shadow picture is dark in the haze behind
    // what stands in it
    const nShadow = LIGHTS.uShadowCount.value;
    for (const inst of this.fixtures.values()) {
      for (const b of inst.beams) b.vol.material.uniforms.uShadow.value = b.shadowSlot >= 0 && b.shadowSlot < nShadow ? b.shadowSlot : -1;
    }
    for (const pj of this.projectors.list) {
      const b = pj.beam;
      b.vol.material.uniforms.uShadow.value = b.shadowSlot >= 0 && b.shadowSlot < nShadow ? b.shadowSlot : -1;
    }
    this._drawScreens(now);

    this.dirty = false;
    this._adapt(now);
    this.lastRender = now;
    this._drawCellMarks();
    this._present(!this.wire && !!this.options.bloom && this.q.ratio > 0.55);
    this._ndiSend(now);
    this._drawLabels();
    // the frame rate shown on the toolbar: pictures drawn in the last second
    this._fpsN++;
    if (now - this._fpsT >= 1000) {
      this.fps = Math.round(this._fpsN * 1000 / (now - this._fpsT));
      this._fpsN = 0;
      this._fpsT = now;
    }
    this._lastDraw = now;
    if (this.opts.onFrame) this.opts.onFrame(now);
  }

  /** The haze the hazers make (atmos.js): each hazer in the rig and what
   *  the desk sends it now; the clouds round the running ones go to the
   *  beams. */
  _stepHaze(dt) {
    const hazers = [];
    for (const inst of this.fixtures.values()) {
      if ((inst.data.body || {}).type !== "atmos") continue;
      const fx = inst.cur && inst.cur.fx;
      const nozzle = inst.sk.nozzle;
      const pos = new THREE.Vector3(), dir = new THREE.Vector3(0, 0, 1);
      if (nozzle) {
        nozzle.getWorldPosition(pos);
        const d = nozzle.userData.dir || [0, 1, 0];
        dir.set(d[0], d[1], d[2]).transformDirection(nozzle.matrixWorld).normalize();
      } else inst.holder.getWorldPosition(pos);
      hazers.push({ head: inst.head, pos, dir, out: fx && fx.fog > 0 ? fx.fog : 0 });
    }
    this._hasHazers = hazers.length > 0;
    if (!this.options.hazeAuto || !hazers.length) {
      if (VOL.cloudCount.value) VOL.cloudCount.value = 0;
      this._hazeBusy = false;
      return;
    }
    const R = this.room;
    this.atmos.step(hazers, Math.max(50, (R.w || 10) * (R.d || 10) * (R.h || 5)), dt);
    const clouds = this.atmos.clouds(this._clouds || (this._clouds = []));
    const n = Math.min(MAX_CLOUDS, clouds.length);
    for (let i = 0; i < n; i++) {
      const c = clouds[i];
      VOL.cloud.value[i].set(c.pos.x, c.pos.y, c.pos.z, c.radius);
      VOL.cloudStr.value[i] = c.str;
    }
    VOL.cloudCount.value = n;
    // while a cloud rolls out or the room fills, the 3D keeps drawing
    this._hazeBusy = n > 0 || hazers.some((h) => h.out > 0.01);
  }

  /** The 3D out as NDI (the desktop app, Settings -> Video): the picture
   *  just drawn, cropped to the output's shape, at its size and rate.  The
   *  labels are on the page, not in the picture, so they don't go out. */
  _ndiSend(now) {
    const o = ndiOut();
    if (!o.on || !o.sending || ndiSender() !== this) return;
    if (now - (this._ndiLast || 0) < 1000 / (o.fps || 30) - 3) return;
    this._ndiLast = now;
    const src = this.renderer.domElement;
    const W = o.w || 1280, H = o.h || 720;
    if (!src.width || !src.height) return;
    const cv = this._ndiCv || (this._ndiCv = document.createElement("canvas"));
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    const ctx = this._ndiCtx || (this._ndiCtx = cv.getContext("2d", { willReadFrequently: true }));
    const k = Math.min(src.width / W, src.height / H);
    const sw = W * k, sh = H * k;
    ctx.drawImage(src, (src.width - sw) / 2, (src.height - sh) / 2, sw, sh, 0, 0, W, H);
    ndiSendFrame(W, H, ctx.getImageData(0, 0, W, H).data, o.fps || 30);
  }

  /** Smooth edges on High: a last pass over the finished picture (FXAA).
   *  (The room is no longer multisampled: the beams read its depth.) */
  _makePipeline(smooth) {
    this._smooth = !!smooth;
    this._postKey = null;
    this._bloomOn = null;
  }

  /** Beams as light in the air: Medium and High, with haze, in the 3D view
   *  (not the flat line drawings), unless auto quality is down to the bone. */
  _volumetric() {
    return this.options.quality !== "fast" && !this.q.lite && !this.wire && !this.options.lightMap
      && (this._hazeNow() > 0.01 || VOL.areaCount.value > 0 || VOL.cloudCount.value > 0);
  }

  /** The room's haze now: the slider's, or on Auto (with a hazer in the
   *  rig) as much of it as the hazers have made (atmos.js). */
  _hazeNow() {
    return this.options.hazeAuto && this._hasHazers ? this.options.haze * this.atmos.level : this.options.haze;
  }

  /** How much haze the 3D shows now, 0..1 (the Haze slider's label). */
  hazeLevel() { return this._hazeNow(); }

  /** The pictures the frame is drawn into, at the canvas's size (the beams
   *  at half of it). */
  _targets() {
    const r = this.renderer;
    const size = r.getDrawingBufferSize(this._dbs || (this._dbs = new THREE.Vector2()));
    const w = Math.max(1, Math.floor(size.x)), h = Math.max(1, Math.floor(size.y));
    if (!this.sceneRT) {
      this.sceneRT = new THREE.RenderTarget(w, h, { type: THREE.HalfFloatType, depthBuffer: true });
      this.sceneRT.depthTexture = new THREE.DepthTexture(w, h);
      this.sceneRT.depthTexture.minFilter = this.sceneRT.depthTexture.magFilter = THREE.NearestFilter;
      this.beamRT = new THREE.RenderTarget(1, 1, { type: THREE.HalfFloatType, depthBuffer: false });
      this._sceneTex = textureNode(this.sceneRT.texture);
      this._beamSize = uniform(new THREE.Vector2(1, 1));
      VOL.depth.value = this.sceneRT.depthTexture;
    }
    if (this.sceneRT.width !== w || this.sceneRT.height !== h) this.sceneRT.setSize(w, h);
    const bw = Math.max(1, Math.ceil(w / 2)), bh = Math.max(1, Math.ceil(h / 2));
    if (this.beamRT.width !== bw || this.beamRT.height !== bh) this.beamRT.setSize(bw, bh);
    this._beamSize.value.set(bw, bh);
  }

  /** Draw the frame: the room (with its depth), the beams in haze over it,
   *  the glow, the true-colour step and, on High, smooth edges. */
  _present(glow) {
    const r = this.renderer;
    this._targets();
    // (the picture's recipe depends on the quality only, never on which
    // beams are lit: a strobe would otherwise rebuild it at every flash)
    const vol = this._volumetric();
    const fx = this._smooth && !this.wire;
    const lux = !!this.options.lightMap;
    if (lux) glow = false;
    const key = `${glow}|${vol}|${fx}|${lux}`;
    if (key !== this._postKey) {
      this._postKey = key;
      let hdr = this._sceneTex;
      if (vol) hdr = hdr.add(beamUpsample(this.beamRT.texture, this._beamSize));
      if (this.bloom) this.bloom.dispose();
      // (the glow sees the light as the standard exposure would: a bright
      // sky, exposed darker, mustn't glow all over - _applyExposure)
      const bs = this._bloomScale || (this._bloomScale = uniform(1));
      this.bloom = glow ? bloom(hdr.mul(bs), 0.55, 0.4, 0.9) : null;
      // the light map's false colours go to the screen as they are (the
      // legend's colours): no exposure, no curve, no glow
      let out = lux ? renderOutput(hdr, THREE.NoToneMapping, THREE.SRGBColorSpace) : pictureOut(glow ? hdr.add(this.bloom.div(bs)) : hdr);
      if (fx) out = fxaa(out);
      this.post.outputColorTransform = false;           // the true-colour step does it (picture.js)
      this.post.outputNode = out;
      this.post.needsUpdate = true;
      this._bloomOn = glow;
    }
    const keep = r.getRenderTarget();
    // the floor's mirror first (the floor's shader reads it): Medium and
    // High, a shiny floor, the camera above it
    const fm = this.built && this.built.mirror;
    if (fm && fm.sheen > 0.05 && this.options.quality !== "fast" && !this.wire && !this.q.lite && !lux
        && this.options.mirror !== false && this.camera.isPerspectiveCamera && this.camera.position.y > 0.05 && !this.options.lightsOnly) {
      const k = this.options.quality === "high" ? 0.5 : 0.25;
      const ms = this._ms || (this._ms = new THREE.Vector2());
      ms.set(this.sceneRT.width * k, this.sceneRT.height * k);
      VOL.z01.value = r.coordinateSystem === THREE.WebGPUCoordinateSystem ? 1 : 0;
      VOL.near.value = this.camera.near;
      VOL.far.value = this.camera.far;
      this.mirror.render(r, this.scene, this.beamScene, this.camera, ms,
        { beams: vol && this.beamScene.children.some((m) => m.visible), hide: fm.hide, steps: 8 });
    } else this.mirror.off();
    r.setRenderTarget(this.sceneRT);
    r.render(this.scene, this.camera);
    if (vol) {
      VOL.z01.value = r.coordinateSystem === THREE.WebGPUCoordinateSystem ? 1 : 0;
      VOL.near.value = this.camera.near;
      VOL.far.value = this.camera.far;
      VOL.steps.value = this.options.quality === "high" ? 24 : 12;
      const clear = r.getClearColor(this._clr || (this._clr = new THREE.Color())), alpha = r.getClearAlpha();
      r.setClearColor(0x000000, 0);
      r.setRenderTarget(this.beamRT);
      if (this.beamScene.children.some((m) => m.visible)) r.render(this.beamScene, this.camera);
      else r.clear();
      r.setClearColor(clear, alpha);
    }
    r.setRenderTarget(keep);
    this.post.render();
  }

  /** A still of the view at print size (the long side `longSide` px), as
   *  a PNG data URL: the scene rendered again at a higher resolution. */
  photo(longSide = 3840) {
    const w = Math.max(1, this.el.clientWidth), h = Math.max(1, this.el.clientHeight);
    const keep = this.q.ratio;
    this.q.ratio = Math.min(4, Math.max(1, longSide / Math.max(w, h)));
    this.resize();
    this._present(!!this.options.bloom);
    const url = this.renderer.domElement.toDataURL("image/png");
    this.q.ratio = keep;
    this.resize();
    this.dirty = true;
    return url;
  }

  /** Record the view as a video stream: the canvas at `longSide` pixels
   *  on its long side (1920 = full HD), 30 frames a second, the picture
   *  quality held steady while it records.  stopRecording() puts it back. */
  startRecording(longSide = 1920, fps = 30) {
    const dom = this.renderer.domElement;
    if (!dom.captureStream) return null;
    const w = Math.max(1, this.el.clientWidth), h = Math.max(1, this.el.clientHeight);
    this.recording = { keep: this.q.ratio };
    this.q.ratio = Math.min(4, Math.max(0.5, longSide / Math.max(w, h)));
    this.resize();
    return dom.captureStream(fps);
  }

  stopRecording() {
    if (!this.recording) return;
    this.q.ratio = this.recording.keep;
    this.recording = null;
    this.resize();
  }

  /** Walk the room at eye height: W A S D / arrows to move (Shift runs),
   *  drag to look round, Esc (or walk(false)) to stop.  Stays inside the
   *  walls. */
  walk(on = true, onEnd = null) {
    const dom = this.renderer.domElement;
    if (on) this._perspective();
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
    if (!list || !list.length) { this._screenGlow = []; return; }
    if (list.some((m) => m.material.userData.video)) this.dirty = true;
    if (now - (this._scrLast || 0) < 60) return;
    this._scrLast = now;
    if (now - (this._glowLast || 0) > 250) {
      this._glowLast = now;
      this._screenGlow = list.filter((m) => !m.userData.screen.projector).map((m) => this._screenLight(m)).filter(Boolean);
      this.projectors.sample();
    }
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

  /** An LED screen as a soft light facing out from its face: what it shows,
   *  averaged (a few pixels of it), times its size. */
  _screenLight(m) {
    const u = m.material.userData || {};
    const src = u.canvas || (u.ndi && u.ndi.canvas) || (u.video) || (m.material.map && m.material.map.image);
    if (!src) return null;
    const c = this._avgCv || (this._avgCv = document.createElement("canvas"));
    c.width = c.height = 4;
    const ctx = this._avgCtx || (this._avgCtx = c.getContext("2d", { willReadFrequently: true }));
    let px;
    try {
      if (src.videoWidth === 0 || src.width === 0) return null;   // nothing playing yet
      ctx.drawImage(src, 0, 0, 4, 4);
      px = ctx.getImageData(0, 0, 4, 4).data;
    } catch (e) { return null; }                                // not readable (another site's picture)
    let r = 0, g = 0, b = 0;
    for (let i = 0; i < px.length; i += 4) { r += px[i]; g += px[i + 1]; b += px[i + 2]; }
    const col = this._c2.setRGB(r / 16 / 255, g / 16 / 255, b / 16 / 255, THREE.SRGBColorSpace);   // to linear
    if (col.r + col.g + col.b < 0.01) return null;
    const geo = m.geometry;
    if (!geo.boundingBox) geo.computeBoundingBox();
    m.updateWorldMatrix(true, false);
    const p = geo.boundingBox.getCenter(new THREE.Vector3()).applyMatrix4(m.matrixWorld);
    const nrm = new THREE.Vector3(0, 0, 1).transformDirection(m.matrixWorld);
    p.addScaledVector(nrm, 0.05);
    const prm = geo.parameters || {};
    const area = Math.max(0.2, (prm.width || 1) * (prm.height || 1));
    const k = 1.2 * area;
    return [p, nrm, col.r * k, col.g * k, col.b * k];
  }

  /** Auto quality: shed resolution when frames run long, win it back later.
   *  A very slow computer (software-drawn 3D: frames of 100-1000 ms) counts
   *  too - it is the one that needs it most; a gap over 2 s is the page
   *  hidden or paused, not a slow frame.  At the bottom (35 %) the surface
   *  detail goes as well (shadows already go below 60 %), and comes back
   *  once frames are quick again. */
  _adapt(now) {
    const q = this.q;
    const dt = now - (q.last || now);
    q.last = now;
    if (this.options.quality !== "auto" || this.recording || dt <= 0 || dt > 2000) return;
    q.ema = q.ema * 0.9 + Math.min(dt, 400) * 0.1;
    const MIN = 0.35;
    if (q.ema > 28) {
      q.fastSince = 0;
      if (!q.slowSince) q.slowSince = now;
      // a frame of 300 ms+ is a struggling computer: act after 3 such frames, not 1.2 s
      else if (now - q.slowSince > (dt > 300 ? Math.min(1200, dt * 3) : 1200)) {
        q.slowSince = 0;
        q.ema = 18;
        if (q.ratio > MIN) {
          // the slower it is, the bigger the step down
          q.ratio = Math.max(MIN, +(q.ratio * (dt > 150 ? 0.6 : 0.8)).toFixed(2));
          this.resize();
        } else if (!q.lite) {
          q.lite = true;
          setDetail(this.scene, false);
        }
      }
    } else if (q.ema < 17) {
      q.slowSince = 0;
      if (!q.fastSince) q.fastSince = now;
      else if (now - q.fastSince > 6000) {
        q.fastSince = 0;
        if (q.lite) {
          q.lite = false;
          setDetail(this.scene, this.options.quality !== "fast");
        } else if (q.ratio < q.cap) {
          q.ratio = Math.min(q.cap, +(q.ratio * 1.15).toFixed(2));
          this.resize();
        }
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
      let pan = px === null ? 0 : lerp(pr[0], pr[1], px) * DEG;
      let tilt = tx === null ? 0 : lerp(tr[0], tr[1], tx) * DEG;
      // its own program: we can't know the real one - a slow wander says
      // "it's running something of its own"; endless rotation turns
      if (L.prog) {
        pan += Math.sin(time * 0.55 + inst.head) * 28 * DEG;
        tilt += Math.sin(time * 0.83 + inst.head * 1.7) * 14 * DEG;
      }
      if (L.spin) {
        if (L.spin.pan) pan += time * L.spin.pan * 200 * DEG;
        if (L.spin.tilt) tilt += time * L.spin.tilt * 160 * DEG;
      }
      if (sk.gdtf) {
        if (moving) { sk.setPan(pan); sk.setTilt(tilt); }
      } else {
        sk.pan.rotation.y = pan;
        sk.tilt.rotation.x = tilt;
      }
      if (sk.cells) {
        // each head of a multi-head light tilts (and pans, if it can) on
        // its own (no motor model)
        sk.cells.forEach((c, k) => {
          const cl = L.cells && L.cells[k];
          const ct = cl && cl.tilt !== null ? cl.tilt : tx;
          c.tilt.rotation.x = ct === null ? 0 : lerp(tr[0], tr[1], ct) * DEG;
          if (c.pan) c.pan.rotation.y = cl && cl.pan !== null ? lerp(pr[0], pr[1], cl.pan) * DEG - pan : 0;
        });
      }
    }
    let a = L.a;
    if (L.prog) {
      // a program runs its own colours and often its own level: a slow
      // colour cycle, visibly lit
      a = Math.max(a, 0.6);
      const hue = (time * 0.12 + inst.head * 0.13) % 1;
      const c = this._c.setHSL(hue, 1, 0.5);
      L.r = c.r; L.g = c.g; L.b = c.b;
    }
    const lk = L.look || {};
    const scroll = lk.cscroll && lk.cscroll.cols && lk.cscroll.cols.length > 1 ? lk.cscroll : null;
    if (scroll) {
      // the colour wheel turning: its colours pass through the beam
      const n = scroll.cols.length, x = ((time * scroll.v) % n + n) % n, i = Math.floor(x), f = x - i;
      const c1 = hexLinear(scroll.cols[i], this._c), r1 = c1.r, g1 = c1.g, b1 = c1.b;
      const c2 = hexLinear(scroll.cols[(i + 1) % n], this._c);
      L.r = lerp(r1, c2.r, f); L.g = lerp(g1, c2.g, f); L.b = lerp(b1, c2.b, f);
    }
    // a real strobe only, at its real speed (look.js strobeLevel)
    const hz = L.hz || lk.shz || 0;
    if (hz > 0 && a > 0.002 && !this.options.calm) this._strobeNow = true;
    a *= strobeLevel(hz, lk.smode, inst.strobeT, time, inst.head, inst.strobe || (inst.strobe = { on: false }), !!this.options.calm);
    inst.strobeT = time;
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
    if (sk.pixels) {
      // a bar's cells / a blinder's bulbs: each its own cell's colour
      // (sent to the graphics card only when a colour changed: 124 lights
      // re-uploading every frame slowed the whole page)
      const px = sk.pixels, n = px.count, cs = L.cells && L.cells.length > 1 ? L.cells : null;
      const k = a * 3, arr = px.instanceColor.array;
      let changed = false;
      for (let i = 0; i < n; i++) {
        const cc = cs ? cs[Math.min(cs.length - 1, Math.floor(i * cs.length / n))] : L;
        const r = 0.04 + cc.r * k, g = 0.04 + cc.g * k, b = 0.045 + cc.b * k, j = i * 3;
        if (Math.abs(arr[j] - r) > 1e-3 || Math.abs(arr[j + 1] - g) > 1e-3 || Math.abs(arr[j + 2] - b) > 1e-3) {
          arr[j] = r; arr[j + 1] = g; arr[j + 2] = b;
          changed = true;
        }
      }
      if (changed) px.instanceColor.needsUpdate = true;
    }
    if (sk.spin && a > 0.002) sk.spin.rotation.y = time * 0.9;   // a derby turns while lit
    void lights;
    return a > 0.002;
  }

  _updateBeams(inst, time, lights) {
    const L = inst.cur;
    const a = inst.level || 0;
    const body = inst.data.body || {};
    const photo = inst.data.photo || {};
    let bmin = (body.beam && body.beam.min) || 20;
    let bmax = (body.beam && body.beam.max) || bmin;
    // the lens angles in the light's own file beat the kind's usual ones;
    // an angle the operator set beats both
    if (photo.lens && photo.lens[1] > 0) {
      const zooms = bmax > bmin;
      bmin = photo.lens[0] > 0 ? photo.lens[0] : photo.lens[1];
      bmax = zooms ? photo.lens[1] : (bmin + photo.lens[1]) / 2;
      if (!zooms) bmin = bmax;
    }
    if (photo.beam > 0) bmin = bmax = photo.beam;
    // how much light it puts out (lumens, from its file, an estimate or
    // the operator's): 3000 lm is the old "one light"
    const flux = (photo.lm > 0 ? photo.lm : 3000) / 3000;
    const washy = /wash|par|fresnel|bar|panel|cyc|blinder|strobe|matrix|tube/.test(body.type || "");
    const zoom = L.beam.zoom;
    const iris = L.beam.iris || 0;
    const frost = L.beam.frost || 0;
    const goboV = L.beam.gobo || 0;
    // focus: sharp in the middle of its travel, softer either side (the
    // sharp point really depends on the throw); frost softens it all
    const blur = Math.min(1, (L.beam.focus === undefined ? 0 : Math.abs(L.beam.focus - 0.5) * 1.4) + frost * 1.2);
    const lk = L.look || {};
    // the prism: the facets the file says (a linear one as -n), turning
    // as its rotation channel says; a file with no words: three
    const facets = lk.prism !== undefined ? lk.prism : (L.beam.prism || 0) > 0.05 ? 3 : 0;
    const prism = facets && lk.plin ? -facets : facets;
    const prot = !facets ? 0 : turnOf(lk.prot, time, time * 0.6);
    // the gobo: indexed, spinning, shaking
    let rot = turnOf(lk.grot, time, (L.beam.gobo_rot || 0) > 0.03 ? time * (L.beam.gobo_rot - 0.03) * 6 : 0);
    if (lk.gshake) rot += Math.sin(time * lk.gshake * 2 * Math.PI) * 0.35;
    // half one colour, half another (a wheel between two filters)
    const split = !lk.cscroll && lk.split ? lk.split.map((h) => { const c = hexLinear(h, this._c); return [c.r, c.g, c.b]; }) : null;
    for (const b of inst.beams) {
      const em = b.em;
      let angle = em.fieldAngle || (zoom === undefined
        ? (bmax > bmin ? (bmin + bmax) / 2 : bmin) : lerp(bmin, bmax, zoom));
      angle *= 1 - iris * 0.6;
      if (em.laser) angle = 0.25;
      if (body.type === "atmos" || angle <= 0 || a <= 0.002) {
        b.mesh.visible = false;
        b.vol.visible = false;
        b.glow.visible = false;
        b.streak.visible = false;
        b.shadowSlot = -1;
        continue;
      }
      em.node.getWorldPosition(b.origin);
      b.dir.copy(em.dir).transformDirection(em.node.matrixWorld).normalize();
      const hit = b.hit || (b.hit = { kind: "", n: new THREE.Vector3() });
      const len = hitDistance(this.planes, this.boxes, b.origin, b.dir, 40, this.segments, hit);
      if (this._wireSegs) {
        // a line drawing: the beam is one thin line in its colour
        const cc0 = em.cell !== undefined && L.cells ? L.cells[em.cell] : null;
        const k = Math.min(1, 0.35 + a);
        this._wireSegs.push([b.origin.clone(), b.dir.clone(), len, (cc0 ? cc0.r : L.r) * k, (cc0 ? cc0.g : L.g) * k, (cc0 ? cc0.b : L.b) * k]);
      }
      const half = (angle / 2) * DEG;
      const r0 = Math.max(0.01, em.radius);
      const r1 = r0 + Math.tan(half) * len;
      b.mesh.position.copy(b.origin);
      // the cone's own x / z are the same right / up the surface shader
      // uses for this beam (materials.js), so a gobo or a split colour
      // reads the same way round in the haze and where it lands
      const rt = this._v1.crossVectors(b.dir, Math.abs(b.dir.y) < 0.99 ? UP : X_AXIS).normalize();
      const u2 = this._v2.crossVectors(rt, b.dir);
      b.mesh.quaternion.setFromRotationMatrix(this._m4.makeBasis(rt, b.dir, u2));
      const u = b.mat.uniforms;
      u.uLen.value = len;
      u.uR0.value = r0;
      u.uR1.value = r1;
      const cc = em.cell !== undefined && L.cells ? L.cells[em.cell] : null;
      const cr = cc ? cc.r : L.r, cg = cc ? cc.g : L.g, cb = cc ? cc.b : L.b;
      if (split) {
        u.uColor.value.setRGB(...split[0]);
        u.uColor2.value.setRGB(...split[1]);
      } else {
        u.uColor.value.setRGB(cr, cg, cb);
        u.uColor2.value.setRGB(cr, cg, cb);
      }
      const narrow = Math.min(3.2, Math.max(0.45, Math.sqrt(22 / Math.max(angle, 1))));
      u.uIntensity.value = a * 0.55 * narrow / Math.max(1, inst.beams.length * 0.6);
      u.uSoft.value = lerp(1.7, 0.55, frost);
      u.uHaze.value = this._hazeNow();
      const volumetric = this._volumetric();
      b.mesh.visible = this._hazeNow() > 0.01 && !volumetric;
      b.reach = len;
      // the beam as light in the air (beams.js): the cone runs on past
      // where its centre lands, to where its far edge does, and the room's
      // depth ends it exactly - at a person, the truss, a wall at any angle
      b.vol.visible = volumetric;
      if (volumetric) {
        const v = b.vol.material.uniforms;
        const k = Math.tan(half);
        let far = len;
        if (len < 39.5) {
          const c = Math.cos(half), sn = Math.sin(half);
          for (const [px, py] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
            const e = this._v3 || (this._v3 = new THREE.Vector3());
            e.copy(b.dir).multiplyScalar(c).addScaledVector(rt, px * sn).addScaledVector(u2, py * sn).normalize();
            far = Math.max(far, hitDistance(this.planes, this.boxes, b.origin, e, 40, this.segments) * c);
          }
        }
        b.reach = far;
        b.vol.position.copy(b.origin);
        b.vol.quaternion.copy(b.mesh.quaternion);
        v.uOrigin.value.copy(b.origin);
        v.uAxis.value.copy(b.dir);
        v.uRight.value.copy(rt);
        v.uUp.value.copy(u2);
        v.uR0.value = r0;
        v.uTan.value = k;
        v.uLen.value = Math.min(40, far * 1.08 + 0.15);
        v.uFade.value = len >= 39.5 ? 1 : 0;
        v.uColor.value.copy(u.uColor.value);
        v.uColor2.value.copy(u.uColor2.value);
        // the light in the beam: shared by a light's heads; the spread
        // over the cone thins it out by itself, so a narrow beam is a
        // bright one and a wide wash a soft one
        v.uPower.value = a * flux / Math.max(1, inst.beams.length);
        // a wash's edge is soft, a spot's crisp; frost softens either
        v.uSoft.value = lerp(washy ? 0.35 : 0.78, 0.02, Math.min(1, frost * 1.2));
        v.uHaze.value = this._hazeNow();
      }

      // head-on lens glow
      const toCam = this._v1.copy(this.camera.position).sub(b.origin).normalize();
      const facing = Math.max(0, toCam.dot(b.dir));
      b.glow.visible = true;
      b.glow.position.copy(b.origin).addScaledVector(b.dir, 0.01);
      const g = r0 * (3 + 12 * Math.pow(facing, 6)) * (0.4 + a);
      b.glow.scale.setScalar(g);
      b.glow.material.color.setRGB(cr, cg, cb).multiplyScalar(0.35 + 1.8 * Math.pow(facing, 4) * a);
      // a lens flare's streak when the lens looks almost straight at the
      // camera (not on Fast quality, not for a laser's pin-point)
      const k = Math.pow(facing, 24) * a;
      b.streak.visible = k > 0.05 && this.options.quality !== "fast" && !em.laser;
      if (b.streak.visible) {
        b.streak.position.copy(b.glow.position);
        b.streak.scale.set(g * 4, g * 0.12, 1);
        b.streak.material.color.setRGB(cr, cg, cb).multiplyScalar(0.45 * k);
      }

      if (inst.head === this.povHead || this.options.lightMap) {   // looking down this beam; the light map
        b.mesh.visible = false;
        b.vol.visible = false;
        b.glow.visible = false;
        b.streak.visible = false;
      }
      let goboId = lk.gscroll ? this._goboScroll(inst, lk.gscroll, time) : this._goboId(inst, goboV);
      // the first wheel open: the second wheel's gobo (a MAC 2000's wheel 2)
      if (!goboId && L.beam.gobo2 !== undefined) goboId = this._goboId(inst, L.beam.gobo2, inst.data.gobos2);
      // an animation wheel in the beam: a break-up pattern that keeps moving
      if (!goboId && L.beam.anim) goboId = 3 + (Math.floor(time * (0.5 + (L.beam.anim_rot || 0.3) * 4)) % 3);
      u.uGobo.value = goboId;                     // the same picture in the haze
      u.uGoboRot.value = rot;
      u.uLook.value.set(blur, prism, prot, split ? 1 : 0);
      if (b.vol.visible) {
        const v = b.vol.material.uniforms;
        v.uGobo.value = goboId;
        v.uGoboRot.value = rot;
        v.uLook.value.set(blur, prism, prot, split ? 1 : 0);
      }
      // its light on the surfaces: the light it puts out, spread over its
      // cone (a narrow beam puts the same light on a smaller circle: a
      // brighter one), shared by its heads; at 1 m from the lens - the
      // surface shader takes the distance into account
      const share = a * flux / Math.max(1, inst.beams.length);
      const spreadK = Math.max(0.03, Math.tan(half));
      const power = Math.min(600, 10.5 * share / (Math.PI * spreadK * spreadK));
      const cone = { cosO: Math.cos(half), cosI: Math.cos(half * lerp(0.72, 0.25, frost)), gobo: goboId, rot, blur, prism, prot };
      // a split beam lands as two half pools, each its own colour
      const bi = inst.beams.indexOf(b);
      for (const [side, c] of split ? [[1, split[0]], [2, split[1]]] : [[0, [cr, cg, cb]]]) {
        lights.push({
          pos: b.origin, dir: b.dir, r: c[0] * power, g: c[1] * power, b: c[2] * power, ...cone,
          side, weight: share * (c[0] + c[1] + c[2]), beam: b, head: inst.head, key: inst.head + ":" + bi + ":" + side,
          reach: b.reach || len, soft: washy || frost > 0.3 ? 3 : 1, cr: c[0], cg: c[1], cb: c[2], share,
          hit: len < 39.5 ? hit : null, hitAt: len,
        });
      }
    }
  }

  /** Which gobo a light shows: the picture in its file for the slot the
   *  channel is in (100 + atlas cell), open (0), or - when the file names
   *  no pictures, or one can't be had - a drawn pattern (1..7). */
  _goboId(inst, v, wheel) {
    const rows = wheel || inst.data.gobos;
    if (rows && rows.length && this.gobos) {
      const dmx = Math.round(v * 255);
      const row = rows.find((r) => dmx >= r[0] && dmx <= r[1]);
      if (!row) return 0;                       // between pictures: open, or a spin range
      if (!row[2]) return 0;                    // the file's "Open" slot
      if (row[2] === "-") return 1 + (rows.indexOf(row) % 7);   // named, no picture: a drawn pattern
      const cell = this.gobos.cell(row[2]);
      if (cell >= 0) return 100 + cell;
      return cell === -1 ? 0 : 1 + (rows.indexOf(row) % 7);   // loading: open for a moment
    }
    return v > 0.06 ? 1 + (Math.floor(v * 7.99) % 7) : 0;
  }

  /** A gobo wheel turning: its gobos pass through the beam one by one. */
  _goboScroll(inst, speed, time) {
    const rows = (inst.data.gobos || []).filter((r) => r[2]);
    if (!rows.length) return 1 + (Math.floor(Math.abs(time * speed)) % 7);
    const n = rows.length, i = ((Math.floor(time * speed) % n) + n) % n;
    return this._goboId(inst, (rows[i][0] + rows[i][1]) / 2 / 255);
  }

  /**
   * Every lit beam to the surfaces (lightgrid.js).  The heads of one light
   * that point the same way in the same colour (the cells of a bar on one
   * look) go as one light; the brightest get a shadow picture; the pools
   * they make, and the LED screens, light the room back a little.
   */
  _uploadLights(lights) {
    const groups = new Map();
    for (const l of lights) {
      const key = l.head + "," + Math.round(l.dir.x * 20) + "," + Math.round(l.dir.y * 20) + "," + Math.round(l.dir.z * 20)
        + "," + l.gobo + "," + l.prism + "," + (l.side || 0) + "," + Math.round(l.cosO * 200)
        + "," + Math.round(l.cr * 50) + "," + Math.round(l.cg * 50) + "," + Math.round(l.cb * 50);
      const g = groups.get(key);
      if (!g) {
        groups.set(key, { ...l, pos: l.pos.clone().multiplyScalar(l.weight || 1e-6), dir: l.dir.clone(), wsum: l.weight || 1e-6,
          beams: [l.beam], key: l.key });
      } else {
        g.pos.addScaledVector(l.pos, l.weight || 1e-6);
        g.wsum += l.weight || 1e-6;
        g.r += l.r; g.g += l.g; g.b += l.b;
        g.weight += l.weight;
        g.share += l.share;
        g.reach = Math.max(g.reach, l.reach);
        g.beams.push(l.beam);
      }
    }
    const list = [...groups.values()].sort((x, y) => y.weight - x.weight);
    for (const g of list) g.pos.multiplyScalar(1 / g.wsum);
    this._lightList = list;                     // (the light meter reads the same lights: luxAt)
    const q = this.options.quality;
    // shadow pictures for the brightest: High 16, Medium 6, none on Low
    // (or when the view is kept light)
    const shadowSlots = this.wire || this.options.shadows === false || q === "fast" || this.q.ratio <= 0.6 ? 0 : q === "high" ? 16 : 6;
    // a light inside something solid (under the deck, in the booth): its
    // shadow picture starts where its beam comes out, as the beam does
    for (const g of list.slice(0, shadowSlots)) {
      g.near = 0;
      for (const bx of this.boxes || []) {
        if (!bx.containsPoint(g.pos)) continue;
        const ray = this._ray || (this._ray = new THREE.Ray());
        ray.set(g.pos, g.dir);
        const out = ray.intersectBox(bx, this._v3 || (this._v3 = new THREE.Vector3()));
        if (out) g.near = Math.max(g.near, out.distanceTo(g.pos) + 0.05);
      }
    }
    if (shadowSlots) this.shadows.update(list, shadowSlots, this._shadowRoom);
    else { this.shadows.off(); for (const g of list) g.slot = -1; }
    for (const l of lights) if (l.beam) l.beam.shadowSlot = -1;
    for (const g of list) for (const bm of g.beams) if (bm && bm.shadowSlot < 0) bm.shadowSlot = g.slot;
    this.grid.update(list, this.room, q === "fast" ? 12 : q === "high" ? 64 : 32);
    this._bounce(list);
    this.lightStats = { ...this.grid.stats, shadows: LIGHTS.uShadowCount.value, bounce: LIGHTS.uVplCount.value };
  }

  /** Light bounced back into the room: the brightest pools (their colour
   *  times the surface's - a black floor sends little back, a white wall a
   *  lot) and the LED screens, as soft lights facing out from where they
   *  are.  High: 12 pools, Medium 6, Low none. */
  _bounce(list) {
    const q = this.options.quality;
    const off = this.wire || q === "fast" || this.options.bounce === false;
    const most = off ? 0 : q === "high" ? 12 : 6;
    const alb = (this.built && this.built.albedo) || {};
    const out = [];
    if (most) {
      const cand = [];
      for (const g of list) {
        if (!g.hit || !g.hit.kind) continue;
        const a = alb[g.hit.kind] || alb.box;
        if (!a) continue;
        const lum = 0.2126 * a.r + 0.7152 * a.g + 0.0722 * a.b;
        cand.push({ g, a, w: g.weight * lum });
      }
      cand.sort((x, y) => y.w - x.w);
      for (const { g, a } of cand.slice(0, most)) {
        const p = g.pos.clone().addScaledVector(g.dir, g.hitAt).addScaledVector(g.hit.n, 0.05);
        const k = 2.5 * g.share;
        out.push([p, g.hit.n, g.cr * a.r * k, g.cg * a.g * k, g.cb * a.b * k]);
      }
    }
    // the LED screens: what they show lights what's in front of them
    if (!off) for (const sg of this._screenGlow || []) if (out.length < MAX_BOUNCE) out.push(sg);
    const n = Math.min(MAX_BOUNCE, out.length);
    for (let i = 0; i < n; i++) {
      const [p, nrm, r, g, b] = out[i];
      LIGHTS.uVplPos.value[i].copy(p);
      LIGHTS.uVplNrm.value[i].copy(nrm);
      LIGHTS.uVplCol.value[i].set(r, g, b);
    }
    LIGHTS.uVplCount.value = n;
  }

  /** The lights showing a drawn placeholder instead of their real 3D:
   *  their tag says so (hover or select one). */
  setDrawn(heads) {
    this.drawn = heads instanceof Set ? heads : new Set(heads || []);
  }

  _drawLabels() {
    // rings mark a big selection; a cloud of tags would hide the rig
    const want = new Set(this.options.labels && this.selected.size <= 12 ? this.selected : []);
    // a light running its own program says so (its colours and moves are a stand-in)
    if (this.options.labels) for (const inst of this.fixtures.values()) if (inst.cur && inst.cur.prog && want.size < 16) want.add(inst.head);
    if (this.options.labels && this.hover !== null && this.hover !== undefined) want.add(this.hover);
    const peek = this.peek || new Set();
    for (const head of peek) if (want.size < 32) want.add(head);
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
      const prog = inst.cur && inst.cur.prog ? `  ▶ ${inst.cur.prog}` : "";
      const drawn = !!(this.drawn && this.drawn.has(head));
      const text = (full ? `#${head}  ${brand}${d.model || ""}`.trim() + (drawn ? "  · placeholder 3D" : "") : `#${head}`) + prog;
      const cls = "stage-tag" + (this.selected.has(head) ? " sel" : "") + (drawn ? " drawn" : "");
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
      const peeked = peek.has(inst.head) && !this.selected.has(inst.head);
      const on = this.selected.has(inst.head) || peeked;
      if (on && !inst.ring) {
        inst.ring = new THREE.Mesh(new THREE.RingGeometry(inst.sk.radius * 1.05, inst.sk.radius * 1.2, 40),
          new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.9, side: THREE.DoubleSide, toneMapped: false }));
        inst.ring.rotation.x = -Math.PI / 2;
        inst.holder.add(inst.ring);
      }
      if (this.wire && inst.wireSel !== this.selected.has(inst.head)) {
        inst.wireSel = this.selected.has(inst.head);
        wireTint(inst.holder, inst.wireSel ? WIRE_SEL : WIRE);
      }
      if (inst.ring) {
        inst.ring.userData.wireSkip = true;
        inst.ring.visible = on;
        inst.ring.material.color.setHex(peeked ? 0xf5a524 : 0x38bdf8);
        inst.ring.position.y = inst.hung ? 0.02 : 0.01;
      }
    }
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this._raf);
    ndiOffer(this, false);
    if (this._ndiOff) this._ndiOff();
    releaseScreens(this.built);
    if (this.sceneRT) { this.sceneRT.dispose(); this.beamRT.dispose(); }
    this.mirror.dispose();
    if (this.resizeObserver) this.resizeObserver.disconnect();
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
    }
    if (this.labels) this.labels.remove();
  }
}
