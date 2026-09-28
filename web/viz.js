/* Jarvis stage visualiser - dependency-free WebGL volumetric preview.
   Draws the stage, the room, the rig, the fixtures and their coloured beams
   as a real 3D scene (raw WebGL1, no libraries, no build step). Two modes:

     - preview (app.js): steps through designed cues at their real fade
       times, client-side only, so nothing reaches the rig.
     - live (console.js): the server owns playback and this file eases
       between the values on its per-tick look feed, which is why the rig
       is editable here (drag/pick) and why opts.venue draws the room.

   This file never sends DMX; output is the engine's job alone.

   - Stage: floor deck + open-front room box (back wall, roof frame, metre
     grid), box truss hanging from the roof on drop rods, moving-head
     fixture bodies (base -> yoke -> head) aimed by their landing point.
   - Beams: a cone per fixture that always lands ON the deck (floor heads
     wash the ceiling instead), shaded volumetrically - depth fade, fresnel
     edge falloff, scrolling value-noise haze, additive blending.
   - Head numbers ride on a transparent 2D overlay above the GL canvas so
     they stay crisp and still work in `still` mode.
   - No WebGL? Everything degrades to a 2D projected poster (console.warn).
   Same public contract as always: window.Viz = { create, landing }. */
(function () {
  "use strict";

  // beam spread = full width of the cone per metre of length
  const ROLE = {
    wash: { spread: 0.90, glow: 0.9, hex: "#8b5cf6" },
    par: { spread: 0.80, glow: 0.9, hex: "#ff5fa2" },
    bar: { spread: 1.10, glow: 0.8, hex: "#2ee66b" },
    spot: { spread: 0.45, glow: 1.0, hex: "#ff8a2a" },
    beam: { spread: 0.16, glow: 1.2, hex: "#3ad6ff" },
    generic: { spread: 0.55, glow: 0.9, hex: "#e9efff" },
  };
  const ROLE_OFF = {
    wash: "#8b5cf6", par: "#8b5cf6", bar: "#2ee66b",
    spot: "#ff8a2a", beam: "#3ad6ff", generic: "#94a3b8",
  };

  /* Fixture BODY shapes, so a moving head does not look like a PAR.
   *
   * Keyed by manufacturer + model substring, lower-cased, most specific
   * first; the engine sends the real manufacturer/model on every head
   * (see app/engine.py snapshot), so the 3D view can draw a yoke-and-base
   * spot, a wash bar, a floor par, or a strobe - which is what an operator
   * actually recognises at a glance.  Anything unknown falls back to the
   * role shape, so a newly imported GDTF still renders sensibly.
   *
   * Shapes: base (clamp), yoke (U), head (tilted can), tube (long bar),
   * panel (flat wash), box.  All drawn in the fixture's own local frame,
   * projected by the same matrix as the room, so they stay put when the
   * camera orbits. */
  const BODY_RULES = [
    // Chauvet moving heads: yoke + base + tilted head
    { match: ["intimidator", "spot 260"], shape: "yoke", beam: true },
    { match: ["intimidator"], shape: "yoke", beam: true },
    { match: ["spot", "moving head"], shape: "yoke", beam: true },
    { match: ["beam"], shape: "tube" },
    { match: ["bar", "batten", "strip"], shape: "tube" },
    { match: ["wash"], shape: "panel" },
    { match: ["par"], shape: "can" },
    { match: ["strobe"], shape: "panel" },
    { match: ["blinder", "strobe"], shape: "panel" },
  ];
  const SHAPE_BY_ROLE = {
    wash: "panel", beam: "tube", spot: "yoke", bar: "tube",
    par: "can", generic: "can",
  };

  /* Which channels a head actually has, from the engine's role map.
   * Drives the body (a head with pan/tilt gets a yoke whether or not we
   * recognise the model). */
  function bodyFor(f) {
    const map = (f.map || []).map((r) => String(r).toLowerCase());
    const has = (r) => map.indexOf(r) >= 0;
    const key = ((f.manufacturer || "") + " " + (f.model || ""))
      .toLowerCase();
    for (let i = 0; i < BODY_RULES.length; i++) {
      const rule = BODY_RULES[i];
      if (rule.match.every((m) => key.indexOf(m) >= 0)) {
        return rule;
      }
    }
    // fall back to what the channels say, not to the role name alone
    if (has("pan") || has("tilt")) return { shape: "yoke", beam: true };
    if (has("gobo") && has("zoom")) return { shape: "yoke", beam: true };
    if (has("wheel") || has("strobe")) return { shape: "yoke", beam: true };
    if (has("shutter")) return { shape: "can" };
    if (map.length >= 8) return { shape: "panel" };
    return { shape: SHAPE_BY_ROLE[f.role] || "can" };
  }

  const TAU = Math.PI * 2;
  const HALF_PI = Math.PI / 2;
  const DEG = Math.PI / 180;
  // A moving head's tilt channel sweeps 270 degrees, and which end of that
  // sweep is "home" is a property of the manufacturer that the profile
  // does not record.  So the mapping is fixed and stated rather than
  // guessed per fixture: tilt 0..1 -> elevation -90..+180 degrees above
  // horizontal (straight down, through horizontal and straight up, ending
  // facing back downstage).  Pan 0..1 -> a full 360 turn, 0 pointing
  // upstage, which is the same direction the geometric fallback uses at
  // pan 0 so a head keeps pointing where it did when aim arrives.
  const TILT_SPAN = 270;

  function hexRgb(hex) {
    const h = String(hex || "#f4f7ff").replace("#", "");
    const s = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
    if (s.length !== 6 || /[^0-9a-f]/i.test(s)) return [244, 247, 255];
    const n = parseInt(s, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function rgba(hex, a) {
    const [r, g, b] = hexRgb(hex);
    return "rgba(" + r + "," + g + "," + b + "," +
      Math.max(0, Math.min(1, a)).toFixed(3) + ")";
  }
  function mixHex(h1, h2, t) {
    const a = hexRgb(h1), b = hexRgb(h2);
    const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
    return "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");
  }
  // same mix, but as float rgb triples (for GL vertex colours)
  function mixRGB(h1, h2, t) {
    const a = hexRgb(h1), b = hexRgb(h2);
    return [
      (a[0] + (b[0] - a[0]) * t) / 255,
      (a[1] + (b[1] - a[1]) * t) / 255,
      (a[2] + (b[2] - a[2]) * t) / 255,
    ];
  }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function smooth(t) { return t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  /* ------------------------------------------------------------ mat4
     Minimal column-major helpers (GLSL convention). mMul always renders
     through a scratch matrix, so overlapping arguments are safe. */
  function m4() { return new Float32Array(16); }
  const _mt = new Float32Array(16);
  function mIdent(o) {
    o[0] = 1; o[1] = 0; o[2] = 0; o[3] = 0;
    o[4] = 0; o[5] = 1; o[6] = 0; o[7] = 0;
    o[8] = 0; o[9] = 0; o[10] = 1; o[11] = 0;
    o[12] = 0; o[13] = 0; o[14] = 0; o[15] = 1;
    return o;
  }
  function mMul(o, a, b) {
    for (let c = 0; c < 4; c++) {
      for (let r = 0; r < 4; r++) {
        _mt[c * 4 + r] =
          a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] +
          a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
      }
    }
    o.set(_mt);
  }
  // Perspective built straight from the visualiser's focal length:
  //   f = focal / (H/2), and m[9] = 0.1 shifts the principal point so the
  //   target lands at 0.55H exactly like the old canvas projector did.
  function mPersp(o, f, aspect, near, far, yOff) {
    o.fill(0);
    o[0] = f / aspect;
    o[5] = f;
    o[9] = yOff;
    o[10] = (far + near) / (near - far);
    o[11] = -1;
    o[14] = (2 * far * near) / (near - far);
  }
  function mTranslate(o, x, y, z) {
    mIdent(o); o[12] = x; o[13] = y; o[14] = z;
  }
  // rotation about X: y' = y c - z s, z' = y s + z c (old pitch convention)
  function mRotX(o, a) {
    const c = Math.cos(a), s = Math.sin(a);
    mIdent(o); o[5] = c; o[6] = s; o[9] = -s; o[10] = c;
  }
  // rotation about Y in the OLD projector's sign convention:
  // x' = x c - z s, z' = x s + z c
  function mRotY(o, a) {
    const c = Math.cos(a), s = Math.sin(a);
    mIdent(o); o[0] = c; o[2] = s; o[8] = -s; o[10] = c;
  }
  function mFlipZ(o) { mIdent(o); o[10] = -1; }
  // standard Y rotation (x' = x c + z s) - used for the FIXTURE model chain
  // (pan), which is independent of the view's old sign convention
  function mRotYStd(o, a) {
    const c = Math.cos(a), s = Math.sin(a);
    mIdent(o); o[0] = c; o[2] = -s; o[8] = s; o[10] = c;
  }

  /* -------------------------------------------------- scene predicates */
  // Hanging vs floor: floor heads sit on the deck, everything else hangs
  // from the roof/truss above (kind is authoritative, y is the fallback).
  function hanging(f) {
    return f.kind ? f.kind !== "floor" : f.y > 1.2;
  }

  // Where a head's beam lands. Hanging heads throw DOWN and always land ON
  // the deck - clamped inside the stage - so a pool never appears off the
  // floor. Floor heads throw UP and wash the ceiling. Coordinates are
  // centred: x in [-w/2, +w/2], z in [0, d] - same convention as layouts.py.
  function landing(f, w, d, ceil) {
    if (!hanging(f)) {
      return [f.x * 0.85, ceil || 6.4, Math.max(-1, f.z - d * 0.3)];
    }
    return [clamp(f.x * 0.9, -w / 2 + 0.3, w / 2 - 0.3), 0,
            clamp(f.z + d * 0.42, 0.3, d - 0.3)];
  }

  /* ------------------------------------------------------- GLSL sources */
  const PREC =
    "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n" +
    "#else\nprecision mediump float;\n#endif\n";

  const VS_SOLID =
    "attribute vec3 aPos;\n" +
    "attribute vec4 aCol;\n" +
    "uniform mat4 uVP;\n" +
    "varying vec4 vCol;\n" +
    "void main() { vCol = aCol; gl_Position = uVP * vec4(aPos, 1.0); }\n";
  const FS_SOLID = PREC +
    "varying vec4 vCol;\n" +
    "void main() { gl_FragColor = vCol; }\n";

  /* GDTF fixture models.
   *
   * A per-node model matrix rather than baking transforms into the vertices,
   * because the vertices are uploaded ONCE per definition and shared by every
   * instance of that type - which is the entire point of the definition
   * cache.  Baking would mean re-uploading 856 triangles per head per frame.
   *
   * Normals come from the upper 3x3 of uModel.  That is only correct because
   * the transform is a rotation plus a UNIFORM scale: uniform scale does not
   * change a normal's direction, so normalising in the shader is enough.  A
   * non-uniform scale would need the inverse transpose, and fitToProfile
   * deliberately never produces one - see the note there about squashing a
   * fixture to fit its declared box.
   *
   * Lighting is deliberately cheap: one key direction, a hemispheric fill,
   * and a rim.  This is a visualiser, not a renderer - but a fixture drawn
   * with flat colour reads as a cardboard cut-out, and the whole point of
   * using the real model is that it looks like the real thing. */
  const VS_MESH =
    "attribute vec3 aPos;\n" +
    "attribute vec3 aNrm;\n" +
    "uniform mat4 uVP;\n" +
    "uniform mat4 uModel;\n" +
    "varying vec3 vN;\n" +
    "varying vec3 vW;\n" +
    "void main() {\n" +
    "  vec4 w = uModel * vec4(aPos, 1.0);\n" +
    "  vW = w.xyz;\n" +
    "  vN = mat3(uModel) * aNrm;\n" +
    "  gl_Position = uVP * w;\n" +
    "}\n";
  const FS_MESH = PREC +
    "uniform vec3 uCam;\n" +
    "uniform vec3 uColor;\n" +
    "uniform vec3 uEmit;\n" +
    "uniform float uSel;\n" +
    "varying vec3 vN;\n" +
    "varying vec3 vW;\n" +
    "void main() {\n" +
    "  vec3 n = normalize(vN);\n" +
    "  vec3 v = normalize(uCam - vW);\n" +
    "  if (dot(n, v) < 0.0) n = -n;          // two-sided: models are open\n" +
    "  const vec3 key = vec3(0.35, 0.82, 0.45);\n" +
    "  float lam = max(dot(n, key), 0.0);\n" +
    "  float sky = 0.5 + 0.5 * n.y;\n" +
    "  float rim = pow(1.0 - max(dot(n, v), 0.0), 2.5);\n" +
    "  vec3 c = uColor * (0.16 + 0.62 * lam + 0.30 * sky) + vec3(rim) * 0.16;\n" +
    // A lit fixture throws its own colour back off the housing, which is
    // most of what tells the operator that head 17 is the one in the cue.
    "  c += uEmit * (0.22 + 0.55 * max(dot(n, normalize(uEmit + 1e-4)), 0.0));\n" +
    "  c = mix(c, vec3(0.42, 0.86, 1.0), uSel * 0.55);\n" +
    "  gl_FragColor = vec4(c, 1.0);\n" +
    "}\n";

  const VS_BG =
    "attribute vec2 aP;\n" +
    "varying float vS;\n" +
    "void main() { vS = 0.5 - aP.y * 0.5; gl_Position = vec4(aP, 0.0, 1.0); }\n";
  const FS_BG = PREC +
    "varying float vS;\n" +
    "void main() {\n" +
    "  vec3 c0 = vec3(0.0157, 0.0235, 0.0431);\n" +   // #04060b
    "  vec3 c1 = vec3(0.0235, 0.0353, 0.0667);\n" +   // #060911
    "  vec3 c2 = vec3(0.0314, 0.0471, 0.0824);\n" +   // #080c15
    "  vec3 c = vS < 0.6 ? mix(c0, c1, vS / 0.6) : mix(c1, c2, (vS - 0.6) / 0.4);\n" +
    "  gl_FragColor = vec4(c, 1.0);\n" +
    "}\n";

  // One shared unit "taper tube" mesh drives every beam; per-beam uniforms
  // place it: apex at the fixture, axis along the landing direction.
  const VS_BEAM =
    "attribute vec2 aAng;\n" +          // (cos, sin) around the axis
    "attribute float aT;\n" +           // 0 at the fixture, 1 at the landing
    "uniform mat4 uVP;\n" +
    "uniform vec3 uO, uD, uR, uF;\n" +  // origin, beam dir, plane basis
    "uniform float uLen, uRad0, uRad1;\n" +
    "varying vec3 vW, vN;\n" +
    "varying float vT;\n" +
    "void main() {\n" +
    "  float t = aT;\n" +
    "  float r = mix(uRad0, uRad1, t);\n" +
    "  vec3 radial = uR * aAng.x + uF * aAng.y;\n" +
    "  vec3 p = uO + uD * (t * uLen) + radial * r;\n" +
    "  vec3 dT = uD * uLen + radial * (uRad1 - uRad0);\n" +
    "  vec3 tang = (-uR * aAng.y + uF * aAng.x) * max(r, 0.001);\n" +
    "  vec3 n = cross(dT, tang);\n" +
    "  float nl = length(n);\n" +
    "  n = nl > 1e-5 ? n / nl : radial;\n" +
    "  if (dot(n, radial) < 0.0) n = -n;\n" +
    "  vW = p; vN = n; vT = t;\n" +
    "  gl_Position = uVP * vec4(p, 1.0);\n" +
    "}\n";
  const FS_BEAM = PREC +
    "varying vec3 vW, vN;\n" +
    "varying float vT;\n" +
    "uniform vec3 uColor, uCam;\n" +
    "uniform float uAlpha, uTime;\n" +
    "float hash13(vec3 p) {\n" +
    "  p = fract(p * 0.1031);\n" +
    "  p += dot(p, p.zyx + 31.32);\n" +
    "  return fract((p.x + p.y) * p.z);\n" +
    "}\n" +
    "float vnoise(vec3 x) {\n" +
    "  vec3 i = floor(x), f = fract(x);\n" +
    "  f = f * f * (3.0 - 2.0 * f);\n" +
    "  float a = mix(mix(hash13(i), hash13(i + vec3(1.0, 0.0, 0.0)), f.x),\n" +
    "                mix(hash13(i + vec3(0.0, 1.0, 0.0)),\n" +
    "                    hash13(i + vec3(1.0, 1.0, 0.0)), f.x), f.y);\n" +
    "  float b = mix(mix(hash13(i + vec3(0.0, 0.0, 1.0)),\n" +
    "                    hash13(i + vec3(1.0, 0.0, 1.0)), f.x),\n" +
    "                mix(hash13(i + vec3(0.0, 1.0, 1.0)),\n" +
    "                    hash13(i + vec3(1.0, 1.0, 1.0)), f.x), f.y);\n" +
    "  return mix(a, b, f.z);\n" +
    "}\n" +
    "void main() {\n" +
    "  vec3 V = normalize(uCam - vW);\n" +
    "  float ndv = abs(dot(V, normalize(vN)));\n" +      // fresnel-ish edge
    "  float fres = pow(ndv, 1.4);\n" +                  // bright core, soft rim
    "  float core = pow(ndv, 6.0) * 0.5;\n" +            // hot centre
    "  float att = exp(-vT * 1.8) * (0.5 + 0.5 / (1.0 + vT * 3.0));\n" +
    "  vec3 q = vW * 0.6;\n" +
    "  float n1 = vnoise(q + vec3(uTime * 0.25, -uTime * 0.35, uTime * 0.18));\n" +
    "  float n2 = vnoise(q * 2.7 + vec3(-uTime * 0.5, uTime * 0.42, uTime * 0.31));\n" +
    "  float haze = mix(0.65, 1.35, n1 * 0.65 + n2 * 0.35);\n" +
    "  float a = uAlpha * att * (fres + core) * haze;\n" +
    "  gl_FragColor = vec4(uColor * a, a);\n" +         // additive: depth write off
    "}\n";

  const VS_POOL =
    "attribute vec2 aP;\n" +
    "uniform mat4 uVP;\n" +
    "uniform vec3 uC, uR, uF;\n" +
    "uniform float uRad;\n" +
    "varying vec2 vP;\n" +
    "void main() {\n" +
    "  vP = aP;\n" +
    "  gl_Position = uVP * vec4(uC + (uR * aP.x + uF * aP.y) * uRad, 1.0);\n" +
    "}\n";
  const FS_POOL = PREC +
    "varying vec2 vP;\n" +
    "uniform vec3 uColor;\n" +
    "uniform float uAlpha;\n" +
    "void main() {\n" +
    "  float r = length(vP);\n" +
    "  if (r > 1.0) discard;\n" +
    "  float a = uAlpha * pow(max(0.0, 1.0 - r), 2.2);\n" +
    "  gl_FragColor = vec4(uColor * a, a);\n" +
    "}\n";

  function create(container, concept, opts) {
    opts = opts || {};
    concept = concept || {};
    const stage = concept.stage ||
      { width: 12, depth: 8, structure: "goalpost", fixtures: [] };
    const cues = concept.cues || [];

    /* ------------------------------------------------------------ state */
    // Free camera, Unity-style.  `tgt` is a real point the user owns and
    // moves; it used to be recomputed from the room every frame, which is
    // why the view snapped back the instant anything moved and why there
    // was no way to pan at all.
    let yaw = -0.55, pitch = 0.34, dist = 0;
    let camInit = false;          // has the camera been framed yet?
    let cueIndex = 0, playing = false, destroyed = false;
    let fadeMs = 0, t0 = 0;
    let from = null, to = null, cur = null;
    // live mode: the server owns playback, we ease toward its per-tick
    // look values (see setLooks in the returned API)
    let liveCur = null, liveFrom = null, liveT0 = 0, liveDur = 0;
    let raf = 0, timer = 0, flashTimer = 0, W = 0, H = 0, dpr = 1;
    let resizeObs = null;
    let focal = 736;               // px, recomputed from W on every draw
    let timeNow = 0;
    // flight: a smooth move to a new camera pose, so presets and F-frame
    // glide instead of teleporting (Unity does the same)
    let fly = null;

    // roles that ever appear (fixtures + cue data), refreshed in lookOf so
    // fixtures added after create() (rig editor) still resolve a colour
    let roles = [], seen = {};
    const addRole = (r) => { if (r && !seen[r]) { seen[r] = true; roles.push(r); } };
    function rebuildRoles() {
      roles = []; seen = {};
      (stage.fixtures || []).forEach((f) => addRole(f.role));
      cues.forEach((c) => Object.keys(c.colours || {}).forEach(addRole));
    }

    // camera / projection state (kept for labels, poster and line widths)
    const tgt = [0, 3, 0], eye = [0, 0, 0], fwd = [0, 0, 1];
    const viewM = m4(), projM = m4(), vpM = m4();
    const _m1 = m4(), _m2 = m4(), _m3 = m4(), _m4 = m4();
    const _cn = new Float32Array(24);
    const _ta = new Float32Array(3), _tb = new Float32Array(3),
          _tc = new Float32Array(3);

    // dynamic geometry: one growable vertex stream + an ordered op list
    const SB = { data: new Float32Array(7 * 4096), n: 0 };
    const ops = [];
    let curOp = null;
    const beamList = [];
    let rowsMemo = null, roofMemo = null;   // per-draw memoisation

    // GL objects (null when WebGL is unavailable)
    let gl = null, progs = true;
    let prSolid = null, prBg = null, prBeam = null, prPool = null;
let prMesh = null, hasUint = false;
// The GDTF twin's scene, handed over by the console.  Null until it is, and
// the visualiser behaves exactly as it always did while it is null - see
// setTwin for why that has to be true rather than merely convenient.
let twin = null;
let twinDrawn = 0;
let twinSuppressed = 0;      // 2D bodies stood down for, last frame
let twinDrew2 = 0;           // real model nodes drawn, last frame
let twinSkipped = 0;         // instances skipped, and why
    let bufMain = null, bufBg = null, bufBeam = null, bufPool = null;
    let glCap = 0, BEAM_VERTS = 0, POOL_VERTS = 0;
    const enLocs = [];
    let curProg = 0, curBlend = -1;

    /* -------------------------------------------------------------- DOM */
    const wrapper = document.createElement("div");
    wrapper.style.position = "relative";
    wrapper.style.width = "100%";
    wrapper.style.height = "100%";
    container.appendChild(wrapper);

    let canvas = document.createElement("canvas");
    canvas.className = "viz-canvas";
    wrapper.appendChild(canvas);
    try {
      gl = canvas.getContext("webgl", { alpha: false, antialias: true }) ||
           canvas.getContext("experimental-webgl", { alpha: false, antialias: true });
    } catch (x) { gl = null; }

    // transparent overlay for head numbers (works for GL and poster alike)
    const labels = document.createElement("canvas");
    labels.style.position = "absolute";
    labels.style.left = "0";
    labels.style.top = "0";
    labels.style.width = "100%";
    labels.style.height = "100%";
    labels.style.display = "block";
    labels.style.pointerEvents = "none";
    wrapper.appendChild(labels);
    let lctx = null;
    try { lctx = labels.getContext("2d"); } catch (x) { lctx = null; }

    // 2D fallback poster (its own canvas, or the overlay if a GL context
    // exists but shader setup failed - a canvas cannot switch context type)
    let posterCanvas = null, posterCtx = null, labelsShare = false;
    if (!gl) {
      console.warn("Viz: WebGL unavailable - falling back to the 2D poster view.");
      wrapper.removeChild(canvas);
      canvas = document.createElement("canvas");
      canvas.className = "viz-canvas";
      wrapper.insertBefore(canvas, labels);
      try { posterCtx = canvas.getContext("2d"); } catch (x) { posterCtx = null; }
      posterCanvas = posterCtx ? canvas : null;
    }

    /* --------------------------------------------------- look / cue data */
    function lookOf(cue) {
      rebuildRoles();
      const look = {};
      roles.forEach((r) => {
        look[r] = {
          hex: (cue && cue.colours && cue.colours[r]) || "#f4f7ff",
          a: (cue && cue.intensity ? (cue.intensity[r] || 0) : 0) / 100,
        };
      });
      return look;
    }
    function cloneLook(l) {
      const o = {};
      for (const r in l) o[r] = { hex: l[r].hex, a: l[r].a };
      return o;
    }

    to = lookOf(cues[0]);
    // No cues at all = a layout/rig preview. Light every head in its role
    // colour so the card shows what the rig does instead of a dead all-off
    // rig; real show designs bring their own cue list and ignore this.
    if (!cues.length) {
      roles.forEach((r) => {
        to[r] = { hex: (ROLE[r] && ROLE[r].hex) || "#e9efff", a: 0.72 };
      });
    }
    from = cloneLook(to);
    cur = cloneLook(to);

    /* ---------------------------------------------------------- player */
    function goTo(i, instant) {
      if (!cues.length) return;
      cueIndex = ((i % cues.length) + cues.length) % cues.length;
      const cue = cues[cueIndex];
      from = cloneLook(cur);
      to = lookOf(cue);
      fadeMs = instant ? 0 : Math.max(100, (cue.fade_s || 1) * 1000);
      t0 = performance.now();
      ensureLoop();
      if (opts.onChange) opts.onChange(cueIndex);
      arm();
    }
    function arm() {
      clearTimeout(timer);
      clearTimeout(flashTimer);
      if (!playing || !cues.length) return;
      const cue = cues[cueIndex];
      // preview holds are compressed so a full run stays watchable
      const hold = clamp((cue.hold_s || 6) * 350, 1500, 5000);
      timer = setTimeout(() => goTo(cueIndex + 1), fadeMs + hold);
    }
    function play() {
      if (!cues.length) return;
      playing = true;
      ensureLoop();
      if (opts.onPlay) opts.onPlay(true);
      arm();
    }
    function pause() {
      playing = false;
      clearTimeout(timer);
      if (opts.onPlay) opts.onPlay(false);
    }
    function step(dir) { pause(); goTo(cueIndex + dir); }
    function go(i) { goTo(i); }

    /* ------------------------------------------------------ scene model */
    // Heads that hang from a drawn truss bar. Explicit kind wins; fixtures
    // without a kind fall back to the height test. Floor heads sit on the
    // deck, tower heads drop from the ceiling on their own hanger.
    function onBar(f) {
      return f.kind === "truss" || (!f.kind && f.y > 1.2);
    }

    // Truss bars are derived from the ACTUAL fixture positions: one bar per
    // DEPTH, so the rig is drawn exactly where the layout puts the lights -
    // never on a fixed template line.
    //
    // One bar per DEPTH, not per (depth, height) cluster.  A real truss is
    // one pipe running across the room; two lights hung from it at slightly
    // different heights are still ONE bar.  Clustering on height as well
    // produced a second bar a few metres above the first, so a rig drawn
    // with four hanging lights showed four bars - two of them stacked over
    // the same patch of room, which reads as a modelling error rather than
    // as rigging.  The bar now sits at the height of the HIGHEST light in
    // the row and the rest hang from it on their own droppers.
    function trussRows() {
      if (rowsMemo) return rowsMemo;
      const rows = [];
      (stage.fixtures || [])
        .filter(onBar)
        .sort((a, b) => a.z - b.z || a.x - b.x)
        .forEach((f) => {
          let row = null;
          for (const r of rows) {
            if (Math.abs(r.z - f.z) < 1.6) { row = r; break; }
          }
          if (!row) {
            row = { z: f.z, top: f.y, fs: [] };
            rows.push(row);
          }
          row.fs.push(f);
          row.z = row.fs.reduce((s, x) => s + x.z, 0) / row.fs.length;
          row.top = Math.max(row.top, f.y);
        });
      rows.forEach((r) => {
        r.fs.sort((a, b) => a.x - b.x);
        const a = r.fs[0], b = r.fs[r.fs.length - 1];
        const hw = stage.width / 2;
        r.x0 = clamp(a.x - 0.55, -hw, hw);
        r.x1 = clamp(b.x + 0.55, -hw, hw);
        r.z0 = a.z;
        r.z1 = b.z;
        r.y = r.top;
        r.barY = r.top + 0.45;
      });
      rowsMemo = rows;
      return rows;
    }

    // --- ceiling the whole rig hangs from (club roof / truss grid) --------
    function roofY() {
      if (roofMemo != null) return roofMemo;
      let top = 5.6;
      (stage.fixtures || []).forEach((f) => {
        if (f.kind !== "floor") top = Math.max(top, f.y + 1.15);
      });
      trussRows().forEach((r) => { top = Math.max(top, r.barY + 1.15); });
      roofMemo = top;
      return top;
    }

    // The room: ONE connected open-front box (upstage wall, side walls, roof
    // frame, floor grid that stops at the room edge).
    //
    // When the caller supplies a venue (the operator drew the room), the
    // venue IS the room and this fallback is suppressed - drawing both puts
    // two half-transparent boxes on top of each other and the operator
    // cannot tell which one the beams are landing on.  The venue carries
    // its own floor, grid, walls and truss lines (see drawVenue).
    function hasVenue() {
      const V = opts.venue || {};
      return Number(V.width_m) > 0 && Number(V.depth_m) > 0;
    }
    function roomBox() {
      if (hasVenue()) {
        const V = opts.venue || {};
        return { x0: -Number(V.width_m) / 2, x1: Number(V.width_m) / 2,
                 z0: 0, z1: Number(V.depth_m),
                 y1: Number(V.height_m) > 0 ? Number(V.height_m) : roofY() };
      }
      const hw = stage.width / 2, d = stage.depth;
      return { x0: -(hw + 1.1), x1: hw + 1.1, z0: -1.1, z1: d + 1.6, y1: roofY() };
    }

    /* -------------------------------------------------- camera / sizing */
    function baseDist() { return Math.max(stage.width, stage.depth) * 1.7 + 8; }

    function resize() {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      W = container.clientWidth || 640;
      H = container.clientHeight || 360;
      // keep the pointer offset in step with the layout (see localPoint)
      if (typeof cacheOffset === "function") cacheOffset();
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      canvas.style.width = W + "px";
      canvas.style.height = H + "px";
      labels.width = Math.round(W * dpr);
      labels.height = Math.round(H * dpr);
      if (posterCanvas) {
        posterCanvas.width = Math.round(W * dpr);
        posterCanvas.height = Math.round(H * dpr);
        posterCanvas.style.width = W + "px";
        posterCanvas.style.height = H + "px";
        if (posterCtx) posterCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      if (labelsShare && lctx) lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (gl) gl.viewport(0, 0, canvas.width, canvas.height);
      if (!dist) dist = baseDist();
      poke();
    }

    // The old canvas projector, rebuilt as matrices - identical maths:
    // orbit yaw about Y, pitch about X, target at the middle of the room,
    // focal = max(W, 400) * 1.15, principal point at 0.55H.
    function updateCamera() {
      // `tgt` is only seeded ONCE.  Recomputing it here from the room on
      // every frame is precisely what made the camera "reset": pan was
      // impossible, and the instant the venue or a fixture changed the
      // target snapped back to the middle of the stage.
      if (!camInit) frameRoom();
      if (!dist) dist = baseDist();
      focal = Math.max(W, 400) * 1.15;
      const cy = Math.cos(yaw), sy = Math.sin(yaw);
      const cp = Math.cos(pitch), sp = Math.sin(pitch);
      eye[0] = tgt[0] - dist * cp * sy;
      eye[1] = tgt[1] - dist * sp;
      eye[2] = tgt[2] - dist * cp * cy;
      fwd[0] = tgt[0] - eye[0];
      fwd[1] = tgt[1] - eye[1];
      fwd[2] = tgt[2] - eye[2];
      const fl = Math.hypot(fwd[0], fwd[1], fwd[2]) || 1;
      fwd[0] /= fl; fwd[1] /= fl; fwd[2] /= fl;
      // view = Tz(-dist) . FlipZ . Rx(pitch) . Ry(yaw) . T(-target)
      mTranslate(viewM, -tgt[0], -tgt[1], -tgt[2]);
      mRotY(_m1, yaw); mMul(viewM, _m1, viewM);
      mRotX(_m1, pitch); mMul(viewM, _m1, viewM);
      mFlipZ(_m1); mMul(viewM, _m1, viewM);
      mTranslate(_m1, 0, 0, -dist); mMul(viewM, _m1, viewM);
      mPersp(projM, focal / (H / 2), W / H, 0.1, dist + 600, 0.1);
      mMul(vpM, projM, viewM);
    }

    /* ------------------------------------------------- framing & flight */
    // Put the target on the middle of the room.  Called once, and again only
    // when the operator explicitly asks (reset view / F).
    function frameRoom() {
      tgt[0] = 0;
      tgt[1] = Math.max(2.2, roofY() * 0.45);
      tgt[2] = stage.depth * 0.45;
      dist = baseDist();
      camInit = true;
    }

    // Frame a world-space box: the target goes to its middle and the dolly
    // distance is derived from its size, so a selected head fills the view
    // and a whole rig fits in it.
    function frameBox(min, max, pad) {
      const cx = (min[0] + max[0]) / 2, cy = (min[1] + max[1]) / 2,
            cz = (min[2] + max[2]) / 2;
      const radius = Math.max(0.8, Math.hypot(max[0] - min[0], max[1] - min[1],
                                             max[2] - min[2]) / 2);
      const fit = radius * (pad || 2.6);
      tgt[0] = cx; tgt[1] = cy; tgt[2] = cz;
      dist = clamp(fit, baseDist() * 0.12, baseDist() * 4);
      camInit = true;
    }

    // Frame whatever the caller selects; with no selection, the whole room.
    function frameHeads(heads) {
      const list = (heads && heads.length) ? heads : (stage.fixtures || []);
      if (!list.length) { frameRoom(); return; }
      const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
      for (const f of list) {
        const x = Number(f.x) || 0, y = Number(f.y) || 0, z = Number(f.z) || 0;
        if (x < min[0]) min[0] = x; if (x > max[0]) max[0] = x;
        if (y < min[1]) min[1] = y; if (y > max[1]) max[1] = y;
        if (z < min[2]) min[2] = z; if (z > max[2]) max[2] = z;
      }
      frameBox(min, max, list === stage.fixtures ? 1.35 : 2.6);
    }

    // F: frame the operator's selection if they have one, else the whole
    // rig.  The caller supplies the selection because the visualiser does
    // not own it.
    function frameSelection(heads) {
      frameHeads(heads);
      flyTo({ tgt: tgt, yaw: yaw, pitch: pitch, dist: dist }, 0);
      poke();
      if (opts.onCamera) opts.onCamera(cameraState());
    }

    // Glide to a pose.  Instant jumps lose the operator's sense of place,
    // which is the whole complaint about the view resetting; a short flight
    // keeps the room continuous so the new angle is legible.
    function flyTo(to, ms) {
      if (ms === 0) {
        if (to.tgt) { tgt[0] = to.tgt[0]; tgt[1] = to.tgt[1]; tgt[2] = to.tgt[2]; }
        if (typeof to.yaw === "number") yaw = to.yaw;
        if (typeof to.pitch === "number") pitch = to.pitch;
        if (typeof to.dist === "number") dist = to.dist;
        camInit = true;
        fly = null;
        poke();
        return;
      }
      // shortest way round for yaw, so a preset never spins the long way
      let dyaw = to.yaw - yaw;
      while (dyaw > Math.PI) dyaw -= TAU;
      while (dyaw < -Math.PI) dyaw += TAU;
      fly = {
        t0: performance.now(), dur: Math.max(80, ms || 320),
        from: { tgt: [tgt[0], tgt[1], tgt[2]], yaw: yaw, pitch: pitch,
                dist: dist },
        to: { tgt: to.tgt ? [to.tgt[0], to.tgt[1], to.tgt[2]]
                        : [tgt[0], tgt[1], tgt[2]],
              yaw: yaw + dyaw,
              pitch: typeof to.pitch === "number" ? to.pitch : pitch,
              dist: typeof to.dist === "number" ? to.dist : dist },
      };
      ensureLoop();
    }

    function stepFly(now) {
      if (!fly) return false;
      const u = Math.min(1, (now - fly.t0) / fly.dur);
      const e = u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
      const a = fly.from, b = fly.to;
      for (let i = 0; i < 3; i++) tgt[i] = a.tgt[i] + (b.tgt[i] - a.tgt[i]) * e;
      yaw = a.yaw + (b.yaw - a.yaw) * e;
      pitch = a.pitch + (b.pitch - a.pitch) * e;
      // dolly on a log scale: constant apparent speed at any distance
      dist = Math.exp(Math.log(Math.max(0.2, a.dist))
                      + (Math.log(Math.max(0.2, b.dist))
                         - Math.log(Math.max(0.2, a.dist))) * e);
      if (u >= 1) { fly = null; if (opts.onCamera) opts.onCamera(cameraState()); }
      return true;
    }

    // Named views.  The numbers are the angles a lighting operator expects
    // to find under a key: house left/right, the pit, straight down.
    const VIEWS = {
      home:  { yaw: -0.55, pitch: 0.34 },
      front: { yaw: 0,     pitch: 0.16 },
      left:  { yaw: -1.5708, pitch: 0.22 },
      right: { yaw: 1.5708, pitch: 0.22 },
      back:  { yaw: 3.1416, pitch: 0.22 },
      top:   { yaw: 0,     pitch: 1.4 },
    };
    function applyView(name) {
      const v = VIEWS[name];
      if (!v) return false;
      flyTo({ yaw: v.yaw, pitch: v.pitch }, 0);
      if (name === "top") {
        tgt[0] = 0; tgt[1] = 0; tgt[2] = stage.depth * 0.45;
        dist = Math.max(stage.width, stage.depth) * 1.5 + 4;
        camInit = true;
      }
      poke();
      if (opts.onCamera) opts.onCamera(cameraState());
      return true;
    }

    function cameraState() {
      return { tgt: [tgt[0], tgt[1], tgt[2]], yaw: yaw, pitch: pitch,
               dist: dist };
    }
    function setCamera(s, snap) {
      if (!s) return;
      if (Array.isArray(s.tgt) && s.tgt.length === 3) {
        tgt[0] = Number(s.tgt[0]) || 0;
        tgt[1] = Number(s.tgt[1]) || 0;
        tgt[2] = Number(s.tgt[2]) || 0;
        camInit = true;
      }
      if (typeof s.yaw === "number" && isFinite(s.yaw)) yaw = s.yaw;
      if (typeof s.pitch === "number" && isFinite(s.pitch)) pitch = s.pitch;
      if (typeof s.dist === "number" && s.dist > 0) dist = s.dist;
      flyTo({ yaw: yaw, pitch: pitch, dist: dist, tgt: tgt }, snap ? 0 : 0);
      if (opts.onCamera) opts.onCamera(cameraState());
    }

    function depthOf(x, y, z) {
      return (x - eye[0]) * fwd[0] + (y - eye[1]) * fwd[1] + (z - eye[2]) * fwd[2];
    }
    function scaleAt(x, y, z) { return focal / Math.max(0.4, depthOf(x, y, z)); }
    function project(x, y, z) {
      const w = vpM[3] * x + vpM[7] * y + vpM[11] * z + vpM[15];
      if (w <= 0.05) return null;
      const cx = vpM[0] * x + vpM[4] * y + vpM[8] * z + vpM[12];
      const cy = vpM[1] * x + vpM[5] * y + vpM[9] * z + vpM[13];
      return {
        x: (cx / w * 0.5 + 0.5) * W,
        y: (0.5 - cy / w * 0.5) * H,
        s: focal / w, d: w,
      };
    }

    /* Inverse of a column-major 4x4 (general, Gauss-Jordan on 4 columns).
     * Only needed for pointer picking, so a few allocations per call are
     * fine - this is not on the draw path. */
    function m4Invert(o, m) {
      const a = [];
      for (let c = 0; c < 4; c++) {
        a.push([m[c * 4], m[c * 4 + 1], m[c * 4 + 2], m[c * 4 + 3]]);
      }
      const inv = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
      for (let col = 0; col < 4; col++) {
        // pivot
        let piv = col, best = Math.abs(a[col][col]);
        for (let r = col + 1; r < 4; r++) {
          const v = Math.abs(a[r][col]);
          if (v > best) { best = v; piv = r; }
        }
        if (best < 1e-9) return false;
        if (piv !== col) {
          const t = a[col]; a[col] = a[piv]; a[piv] = t;
          const u = inv[col]; inv[col] = inv[piv]; inv[piv] = u;
        }
        const d = a[col][col];
        for (let k = 0; k < 4; k++) { a[col][k] /= d; inv[col][k] /= d; }
        for (let r = 0; r < 4; r++) {
          if (r === col) continue;
          const f = a[r][col];
          if (!f) continue;
          for (let k = 0; k < 4; k++) {
            a[r][k] -= f * a[col][k];
            inv[r][k] -= f * inv[col][k];
          }
        }
      }
      for (let c = 0; c < 4; c++) {
        for (let r = 0; r < 4; r++) o[c * 4 + r] = inv[c][r];
      }
      return true;
    }

    // transform a clip-space point by a matrix -> [x, y, z, w]
    function tr(m, x, y, z) {
      return [
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
        m[3] * x + m[7] * y + m[11] * z + m[15],
      ];
    }

    /* ------------------------------------------------ geometry builders */
    function sbReset() { SB.n = 0; ops.length = 0; curOp = null; beamList.length = 0; }
    function sbGrow(k) {
      if ((SB.n + k) * 7 <= SB.data.length) return;
      let cap = SB.data.length / 7;
      while (cap < SB.n + k) cap *= 2;
      const nd = new Float32Array(cap * 7);
      nd.set(SB.data);
      SB.data = nd;
    }
    let cr = 1, cg = 1, cb = 1, ca = 1;
    function setColor(hex, a) {
      const c = hexRgb(hex);
      cr = c[0] / 255; cg = c[1] / 255; cb = c[2] / 255;
      ca = a == null ? 1 : a;
    }
    function setColorf(r, g, b, a) { cr = r; cg = g; cb = b; ca = a == null ? 1 : a; }
    function vert(x, y, z) {
      sbGrow(1);
      const o = SB.n * 7, d = SB.data;
      d[o] = x; d[o + 1] = y; d[o + 2] = z;
      d[o + 3] = cr; d[o + 4] = cg; d[o + 5] = cb; d[o + 6] = ca;
      SB.n++;
    }
    function tri(x0, y0, z0, x1, y1, z1, x2, y2, z2) {
      vert(x0, y0, z0); vert(x1, y1, z1); vert(x2, y2, z2);
    }
    function quadP(x0, y0, z0, x1, y1, z1, x2, y2, z2, x3, y3, z3) {
      tri(x0, y0, z0, x1, y1, z1, x2, y2, z2);
      tri(x0, y0, z0, x2, y2, z2, x3, y3, z3);
    }
    // op list: kind 0 = vertex range, 1 = beam, 2 = pool
    function begin(blend) {
      endOp();
      curOp = { k: 0, b: blend, s: SB.n, c: 0 };
      ops.push(curOp);
    }
    function endOp() {
      if (curOp) { curOp.c = SB.n - curOp.s; curOp = null; }
    }
    function pushBeam(bm) { endOp(); ops.push({ k: 1, bm: bm }); }
    function pushPool(pm) { endOp(); ops.push({ k: 2, pm: pm }); }

    // A screen-constant-width line built as a camera-facing world ribbon
    // (WebGL lineWidth is stuck at 1px everywhere, so truss chords need
    // real quads). wpx is the wanted width in CSS pixels.
    function lineSeg(x0, y0, z0, x1, y1, z1, wpx, hex, a) {
      const dx = x1 - x0, dy = y1 - y0, dz = z1 - z0;
      const len = Math.hypot(dx, dy, dz);
      if (len < 1e-6) return;
      let dp = depthOf((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
      if (dp < 0.08) dp = 0.08;
      const hw = (Math.max(1, wpx) * dp) / focal / 2;   // world half-width
      const ux = dx / len, uy = dy / len, uz = dz / len;
      let vx = (x0 + x1) / 2 - eye[0],
          vy = (y0 + y1) / 2 - eye[1],
          vz = (z0 + z1) / 2 - eye[2];
      const vl = Math.hypot(vx, vy, vz) || 1;
      vx /= vl; vy /= vl; vz /= vl;
      // perpendicular on screen = view ray x line direction
      let px = vy * uz - vz * uy, py = vz * ux - vx * uz, pz = vx * uy - vy * ux;
      let pl = Math.hypot(px, py, pz);
      if (pl < 1e-4) {
        if (Math.abs(ux) < 0.9) { px = 0; py = -uz; pz = uy; }
        else { px = uz; py = 0; pz = -ux; }
        pl = Math.hypot(px, py, pz) || 1;
      }
      px = px / pl * hw; py = py / pl * hw; pz = pz / pl * hw;
      const ex = ux * hw, ey = uy * hw, ez = uz * hw;   // caps
      setColor(hex, a);
      tri(x0 - ex - px, y0 - ey - py, z0 - ez - pz,
          x0 - ex + px, y0 - ey + py, z0 - ez + pz,
          x1 + ex + px, y1 + ey + py, z1 + ez + pz);
      tri(x0 - ex - px, y0 - ey - py, z0 - ez - pz,
          x1 + ex + px, y1 + ey + py, z1 + ez + pz,
          x1 + ex - px, y1 + ey - py, z1 + ez - pz);
    }

    function tfp(M, px, py, pz, out) {
      out[0] = M[0] * px + M[4] * py + M[8] * pz + M[12];
      out[1] = M[1] * px + M[5] * py + M[9] * pz + M[13];
      out[2] = M[2] * px + M[6] * py + M[10] * pz + M[14];
    }

    // shaded box in local coords, transformed by M (rotation + translation,
    // no scale - sizes are baked into the half-extents)
    function boxM(M, lx, ly, lz, hx, hy, hz) {
      let k = 0;
      for (let i = 0; i < 8; i++) {
        const px = lx + (i & 1 ? hx : -hx);
        const py = ly + (i & 2 ? hy : -hy);
        const pz = lz + (i & 4 ? hz : -hz);
        _cn[k++] = M[0] * px + M[4] * py + M[8] * pz + M[12];
        _cn[k++] = M[1] * px + M[5] * py + M[9] * pz + M[13];
        _cn[k++] = M[2] * px + M[6] * py + M[10] * pz + M[14];
      }
      const faces = [
        [1, 3, 7, 5, 1, 0, 0], [0, 4, 6, 2, -1, 0, 0],
        [2, 6, 7, 3, 0, 1, 0], [0, 1, 5, 4, 0, -1, 0],
        [4, 5, 7, 6, 0, 0, 1], [0, 2, 3, 1, 0, 0, -1],
      ];
      const r0 = cr, g0 = cg, b0 = cb;
      for (let i = 0; i < 6; i++) {
        const f = faces[i];
        const nx = M[0] * f[4] + M[4] * f[5] + M[8] * f[6];
        const ny = M[1] * f[4] + M[5] * f[5] + M[9] * f[6];
        const nz = M[2] * f[4] + M[6] * f[5] + M[10] * f[6];
        const l = Math.max(0, nx * 0.31 + ny * 0.9 + nz * 0.31);
        const sh = 0.72 + 0.5 * l;
        cr = r0 * sh; cg = g0 * sh; cb = b0 * sh;
        const a = f[0] * 3, b = f[1] * 3, c = f[2] * 3, d = f[3] * 3;
        quadP(_cn[a], _cn[a + 1], _cn[a + 2],
              _cn[b], _cn[b + 1], _cn[b + 2],
              _cn[c], _cn[c + 1], _cn[c + 2],
              _cn[d], _cn[d + 1], _cn[d + 2]);
      }
      cr = r0; cg = g0; cb = b0;
    }

    // small disc in the local XZ plane (the fixture lens), normal = local Y
    function discM(M, lx, ly, lz, r, seg) {
      tfp(M, lx, ly, lz, _ta);
      for (let i = 0; i < seg; i++) {
        const a0 = (i / seg) * TAU, a1 = ((i + 1) / seg) * TAU;
        tfp(M, lx + Math.cos(a0) * r, ly, lz + Math.sin(a0) * r, _tb);
        tfp(M, lx + Math.cos(a1) * r, ly, lz + Math.sin(a1) * r, _tc);
        tri(_ta[0], _ta[1], _ta[2],
            _tb[0], _tb[1], _tb[2],
            _tc[0], _tc[1], _tc[2]);
      }
    }

    /* --------------------------------------------------- beam geometry */
    // Where a beam driven by real pan/tilt channels meets the deck.
    //
    // The geometric path lands wherever the head happens to hang; once the
    // operator points the head somewhere else that landing is a lie.  So the
    // ray is intersected with the plane the head is aimed at - the deck for
    // a hanging head, the grid for a floor unit - and when the aim points
    // away from that plane the beam is simply drawn along its direction
    // until it leaves the room, rather than snapped back to a landing the
    // fixture is not pointing at.
    function aimedLanding(ox, oy, oz, dx, dy, dz, w, d, ceil, hang) {
      const plane = hang ? 0 : (ceil || 6.4);
      const gap = plane - oy;
      let t = (Math.abs(dy) > 0.02) ? gap / dy : -1;
      if (!(t > 0.05) || t > 80) {
        // aimed at the ceiling skyward, or straight along the plane
        t = hang ? Math.max(0.5, oy) : Math.max(0.5, (ceil || 6.4) - oy);
      }
      const m = 0.35;
      return [
        clamp(ox + dx * t, -w / 2 + m, w / 2 - m),
        plane,
        clamp(oz + dz * t, m, d - m),
      ];
    }

    // Landing point, direction, spread and pan/tilt for one fixture - used
    // by both the volumetric beam and the fixture body that aims it.
    function beamOf(f) {
      const meta = ROLE[f.role] || ROLE.generic;
      const w = stage.width, d = stage.depth;
      const ox = f.x, oy = f.y, oz = f.z;
      const hang = hanging(f);
      const ceil = roofY() - 0.05;
      const look = f._look || cur[f.role];
      let E = landing(f, w, d, ceil);
      if (hang) {
        // pull the landing inboard so the WHOLE cone base and its pool stay
        // on the floor
        for (let i = 0; i < 2; i++) {
          const seg = Math.hypot(E[0] - ox, E[1] - oy, E[2] - oz);
          let hw = Math.max(0.06, seg * meta.spread * 0.5);
          hw = Math.min(hw, (w - 0.7) / 2.4, (d - 0.7) / 1.4);
          const mx = 0.35 + hw * 1.2;
          const mz = 0.35 + hw * 0.7;
          E = [clamp(E[0], -w / 2 + mx, w / 2 - mx), 0, clamp(E[2], mz, d - mz)];
        }
      }
      let ex = E[0], ey = E[1], ez = E[2];
      let len = Math.hypot(ex - ox, ey - oy, ez - oz) || 0.001;
      let halfW = Math.min(Math.max(0.06, len * meta.spread * 0.5),
                           (w - 0.7) / 2.4, (d - 0.7) / 1.4);
      let dx = (ex - ox) / len, dy = (ey - oy) / len, dz = (ez - oz) / len;
      // pan/tilt of the pillar chain (base -> yoke -> head)
      let pan, tilt;
      if (hang) {
        pan = Math.atan2(-dx, -dz);
        tilt = Math.acos(clamp(-dy, -1, 1));
      } else {
        pan = Math.atan2(dx, dz);
        tilt = Math.acos(clamp(dy, -1, 1));
      }

      /* ---- driven aim ------------------------------------------------- */
      // pan/tilt arrive on the look row only when the fixture HAS that
      // channel and something is driving it.  Either may be present alone,
      // so each keeps the geometric value for the one that is missing.
      // Before this existed a moving head sat frozen on its landing point
      // while its beam was pointed elsewhere - the head looked broken even
      // though the DMX on the wire was right.
      const hasPan = look && typeof look.pan === "number"
                     && isFinite(look.pan);
      const hasTilt = look && typeof look.tilt === "number"
                      && isFinite(look.tilt);
      if (hasPan || hasTilt) {
        // The FIXTURE'S OWN travel when the profile declares one, the
        // 270-degree convention when it does not.  A Chauvet Intimidator
        // is -117..+117 and a 540-pan head is -270..+270; treating both as
        // 270 drew every beam 12 degrees short at the ends and looked
        // entirely plausible while being wrong.
        const tspan = look.deg && look.deg.tilt;
        const el = hasTilt
          ? (tspan
            ? (tspan[0] + look.tilt * (tspan[1] - tspan[0])) * DEG
            : (look.tilt * TILT_SPAN - 90) * DEG)
          : (hang ? tilt - HALF_PI : HALF_PI - tilt);
        const pspan = look.deg && look.deg.pan;
        const panW = hasPan
          ? (clamp(look.pan, 0, 1) * (pspan ? Math.abs(pspan[1] - pspan[0])
                                           : 360)) * DEG
          : pan;
        const ch = Math.cos(el);
        if (hang) {
          dx = -Math.sin(panW) * ch;
          dy = Math.sin(el);
          dz = -Math.cos(panW) * ch;
        } else {
          dx = Math.sin(panW) * ch;
          dy = Math.sin(el);
          dz = Math.cos(panW) * ch;
        }
        const n = Math.hypot(dx, dy, dz) || 1;
        dx /= n; dy /= n; dz /= n;
        E = aimedLanding(ox, oy, oz, dx, dy, dz, w, d, ceil, hang);
        ex = E[0]; ey = E[1]; ez = E[2];
        len = Math.hypot(ex - ox, ey - oy, ez - oz) || 0.001;
        halfW = Math.min(Math.max(0.06, len * meta.spread * 0.5),
                         (w - 0.7) / 2.4, (d - 0.7) / 1.4);
        // rotation of the pillar chain, as the body expects it: the yoke
        // turns by pan, the head tips by tilt from its own rest axis
        pan = panW;
        tilt = hang ? el + HALF_PI : HALF_PI - el;
      }

      // orthonormal basis around the beam axis: R = ref x D, F = D x R
      // (ref flips to X when the beam is near-vertical so R never dies)
      let rx, ry, rz;
      if (Math.abs(dy) > 0.98) { rx = 0; ry = -dz; rz = dy; }
      else { rx = dz; ry = 0; rz = -dx; }
      let rl = Math.hypot(rx, ry, rz);
      if (rl < 1e-5) { rx = 1; ry = 0; rz = 0; rl = 1; }
      rx /= rl; ry /= rl; rz /= rl;
      const fx = dy * rz - dz * ry, fy = dz * rx - dx * rz, fz = dx * ry - dy * rx;
      return {
        f: f, meta: meta, hang: hang, look: look, aimed: hasPan || hasTilt,
        ox: ox, oy: oy, oz: oz, ex: ex, ey: ey, ez: ez,
        dx: dx, dy: dy, dz: dz, len: len, halfW: halfW,
        rx: rx, ry: ry, rz: rz, fx: fx, fy: fy, fz: fz,
        pan: pan, tilt: tilt,
      };
    }

    /* ------------------------------------------------------- scene build */
    // Everything CPU-side is rebuilt every draw: call sites mutate the
    // stage fixtures (and each fixture's _look) in place and then call
    // redraw(), so there is no signature to trust but the live objects.
    function buildScene() {
      sbReset();
      const R = roomBox();
      const hw = stage.width / 2, d = stage.depth;
      const ry = roofY();
      const fixtures = stage.fixtures || [];
      fixtures.forEach((f) => beamList.push(beamOf(f)));

      // --- back wall (fill) ---
      begin(0);
      setColor("#0f1728", 0.8);
      quadP(R.x0, 0, R.z0, R.x1, 0, R.z0, R.x1, R.y1, R.z0, R.x0, R.y1, R.z0);

      // --- wall outline, roof frame, back posts, floor grid ---
      begin(0);
      setColor("#1d2a44", 1);
      lineSeg(R.x0, 0, R.z0, R.x1, 0, R.z0, 1.2, "#1d2a44", 1);
      lineSeg(R.x1, 0, R.z0, R.x1, R.y1, R.z0, 1.2, "#1d2a44", 1);
      lineSeg(R.x1, R.y1, R.z0, R.x0, R.y1, R.z0, 1.2, "#1d2a44", 1);
      lineSeg(R.x0, R.y1, R.z0, R.x0, 0, R.z0, 1.2, "#1d2a44", 1);
      const rcx = [R.x0, R.x1, R.x1, R.x0], rcz = [R.z0, R.z0, R.z1, R.z1];
      for (let i = 0; i < 4; i++) {
        const j = (i + 1) % 4;
        lineSeg(rcx[i], R.y1, rcz[i], rcx[j], R.y1, rcz[j],
                1.2, "#707e9c", 0.4);
      }
      lineSeg(R.x0, 0, R.z0, R.x0, R.y1, R.z0, 1.2, "#586682", 0.5);
      lineSeg(R.x1, 0, R.z0, R.x1, R.y1, R.z0, 1.2, "#586682", 0.5);
      for (let x = Math.ceil(R.x0); x <= R.x1; x += 1) {
        lineSeg(x, 0, R.z0, x, 0, R.z1, 1, "#0e1626", 1);
      }
      for (let z = Math.ceil(R.z0); z <= R.z1; z += 1) {
        lineSeg(R.x0, 0, z, R.x1, 0, z, 1, "#0e1626", 1);
      }

      // --- the deck sits exactly on that grid ---
      begin(0);
      setColor("#111b30", 0.68);
      quadP(-hw, 0, 0, hw, 0, 0, hw, 0, d, -hw, 0, d);
      begin(0);
      lineSeg(-hw, 0, 0, hw, 0, 0, 1.6, "#2f4a74", 1);
      lineSeg(hw, 0, 0, hw, 0, d, 1.6, "#2f4a74", 1);
      lineSeg(hw, 0, d, -hw, 0, d, 1.6, "#2f4a74", 1);
      lineSeg(-hw, 0, d, -hw, 0, 0, 1.6, "#2f4a74", 1);

      // --- rig: drop rods + box truss at the ACTUAL head positions ---
      begin(0);
      trussRows().forEach((r) => {
        setColor("#4d5871", 1);
        const rw = (x, y, z) => clamp(scaleAt(x, y, z) * 0.035, 1, 3);
        lineSeg(r.x0, ry, r.z0, r.x0, r.barY + 0.22, r.z0,
                rw(r.x0, ry, r.z0), "#4d5871", 1);
        lineSeg(r.x1, ry, r.z1, r.x1, r.barY + 0.22, r.z1,
                rw(r.x1, ry, r.z1), "#4d5871", 1);
        if (r.x1 - r.x0 > 3.5) {
          const mx = (r.x0 + r.x1) / 2, mz = (r.z0 + r.z1) / 2;
          lineSeg(mx, ry, mz, mx, r.barY + 0.22, mz,
                  rw(mx, ry, mz), "#4d5871", 1);
        }
        // box truss: two chords + rungs
        const hh = 0.22;
        const at = (t, y) => [
          r.x0 + (r.x1 - r.x0) * t, y, r.z0 + (r.z1 - r.z0) * t,
        ];
        const t0 = at(0, r.barY + hh), t1 = at(1, r.barY + hh);
        const b0 = at(0, r.barY - hh), b1 = at(1, r.barY - hh);
        const pa = project(t0[0], t0[1], t0[2]), pb = project(t1[0], t1[1], t1[2]);
        if (pa && pb) {
          const px = Math.hypot(pa.x - pb.x, pa.y - pb.y);
          const n = Math.round(Math.hypot(r.x1 - r.x0, r.z1 - r.z0) / 0.75);
          if (px > 64 && n >= 2) {
            const rwid = clamp(pa.s * 0.03, 1, 2);
            for (let i = 0; i <= n; i++) {
              const q = at(i / n, r.barY - hh), u = at(i / n, r.barY + hh);
              lineSeg(q[0], q[1], q[2], u[0], u[1], u[2], rwid, "#4a566f", 1);
            }
          }
          const ow = clamp(pa.s * 0.13, 2.5, 10);
          const iw = Math.max(1, clamp(pa.s * 0.045, 1, 3));
          lineSeg(t0[0], t0[1], t0[2], t1[0], t1[1], t1[2], ow, "#39445a", 1);
          lineSeg(t0[0], t0[1], t0[2], t1[0], t1[1], t1[2], iw, "#5d6a85", 1);
          const q0 = project(b0[0], b0[1], b0[2]);
          const ow2 = q0 ? clamp(q0.s * 0.13, 2.5, 10) : ow;
          const iw2 = q0 ? Math.max(1, clamp(q0.s * 0.045, 1, 3)) : iw;
          lineSeg(b0[0], b0[1], b0[2], b1[0], b1[1], b1[2], ow2, "#39445a", 1);
          lineSeg(b0[0], b0[1], b0[2], b1[0], b1[1], b1[2], iw2, "#5d6a85", 1);
        }
      });
      // side-drop heads (tower kind): one vertical hanger per column
      const done = {};
      fixtures.forEach((f) => {
        if (f.kind !== "tower") return;
        const k = f.x.toFixed(2) + "," + f.z.toFixed(2);
        if (done[k]) return;
        done[k] = 1;
        const col = fixtures.filter((t) =>
          t.kind === "tower" && Math.abs(t.x - f.x) < 0.3 &&
          Math.abs(t.z - f.z) < 0.3);
        const lowest = Math.min.apply(null, col.map((t) => t.y));
        setColor("#4d5871", 1);
        lineSeg(f.x, ry, f.z, f.x, lowest, f.z,
                clamp(scaleAt(f.x, ry, f.z) * 0.035, 1, 3), "#4d5871", 1);
      });

      // --- volumetric beams + landing pools (additive, order-independent) ---
      beamList.forEach((bm) => {
        const look = bm.look;
        if (!look || look.a <= 0.02) return;      // intensity 0 = no beam
        const meta = bm.meta;
        const alpha = look.a * 0.6 * meta.glow;
        const c = hexRgb(look.hex);
        pushBeam({
          ox: bm.ox, oy: bm.oy, oz: bm.oz,
          dx: bm.dx, dy: bm.dy, dz: bm.dz,
          rx: bm.rx, ry: bm.ry, rz: bm.rz,
          fx: bm.fx, fy: bm.fy, fz: bm.fz,
          len: bm.len, rad0: 0.05, rad1: bm.halfW,
          cr: c[0] / 255, cg: c[1] / 255, cb: c[2] / 255, a: alpha,
        });
        // pool where the beam lands (floor pool, or ceiling wash for heads
        // that throw up) - screen radius 1.1x the cone base, min 6px
        const dE = Math.max(0.4, depthOf(bm.ex, bm.ey, bm.ez));
        const sE = focal / dE;
        pushPool({
          cx: bm.ex, cy: bm.ey, cz: bm.ez,
          rad: Math.max(bm.halfW * 1.1, 6 / sE),
          cr: c[0] / 255, cg: c[1] / 255, cb: c[2] / 255,
          a: alpha * (bm.hang ? 0.55 : 0.4),
        });
      });

      // --- fixture stems (drawn over the beams, like the old renderer) ---
      begin(0);
      fixtures.forEach((f) => {
        let ax, ay, az;
        if (onBar(f)) { ax = f.x; ay = f.y + 0.23; az = f.z; }   // truss chord
        else if (hanging(f)) { ax = f.x; ay = ry; az = f.z; }    // ceiling
        else { ax = f.x; ay = 0; az = f.z; }                     // deck
        const wpx = Math.max(1, scaleAt(f.x, f.y, f.z) * 0.022);
        lineSeg(ax, ay, az, f.x, f.y, f.z, wpx, "#4b5a75", 1);
      });

      // --- fixture bodies: base -> yoke -> head, head aims at landing ---
      begin(0);
      fixtures.forEach((f, i) => {
        const bm = beamList[i];
        if (!bm) return;
        const look = bm.look;
        const on = look && look.a > 0.02;
        const col = on ? look.hex : (ROLE_OFF[f.role] || ROLE_OFF.generic);
        // body dark, tinted toward the (lit) stroke colour like the old
        // rounded rect with its coloured outline
        const body = mixRGB("#0d1526", on ? col : "#334155", on ? 0.45 : 0.25);
        const sg = bm.hang ? 1 : -1;    // base sits opposite the beam
        // Mbase = T(base) (bolted to the bar / deck, never rotates)
        mTranslate(_m1, bm.ox, bm.oy + sg * 0.11, bm.oz);
        setColorf(body[0], body[1], body[2], 1);
        boxM(_m1, 0, 0, 0, 0.085, 0.045, 0.085);
        // Myoke = T(O) . Ry(pan)
        mTranslate(_m1, bm.ox, bm.oy, bm.oz);
        mRotYStd(_m2, bm.pan);
        mMul(_m3, _m1, _m2);
        setColorf(body[0] * 1.15, body[1] * 1.15, body[2] * 1.15, 1);
        boxM(_m3, 0.075, sg * 0.05, 0, 0.016, 0.055, 0.026);
        boxM(_m3, -0.075, sg * 0.05, 0, 0.016, 0.055, 0.026);
        // Mhead = T(O) . Ry(pan) . Rx(tilt) - local +/-Y is the beam axis
        mRotX(_m2, bm.tilt);
        mMul(_m4, _m3, _m2);
        setColorf(body[0] * 1.3, body[1] * 1.3, body[2] * 1.3, 1);
        boxM(_m4, 0, 0, 0, 0.06, 0.068, 0.06);
        // lens, facing along the beam
        setColor(col, on ? 0.95 : 0.5);
        discM(_m4, 0, -sg * 0.07, 0, 0.042, 8);
      });
      endOp();
    }

    /* ------------------------------------------------------ GL plumbing */
    function initGL() {
      function sh(type, src) {
        const s = gl.createShader(type);
        gl.shaderSource(s, src);
        gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
          const err = gl.getShaderInfoLog(s);
          gl.deleteShader(s);
          throw new Error(String(err));
        }
        return s;
      }
      function link(vs, fs) {
        const p = gl.createProgram();
        gl.attachShader(p, sh(gl.VERTEX_SHADER, vs));
        gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
        gl.linkProgram(p);
        if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
          throw new Error(String(gl.getProgramInfoLog(p)));
        }
        return p;
      }
      function pack(p, us, as) {
        const o = { p: p, u: {}, a: {} };
        us.forEach((n) => { o.u[n] = gl.getUniformLocation(p, n); });
        as.forEach((n) => { o.a[n] = gl.getAttribLocation(p, n); });
        return o;
      }
      prSolid = pack(link(VS_SOLID, FS_SOLID), ["uVP"], ["aPos", "aCol"]);
      prBg = pack(link(VS_BG, FS_BG), [], ["aP"]);
      prBeam = pack(link(VS_BEAM, FS_BEAM),
        ["uVP", "uO", "uD", "uR", "uF", "uLen", "uRad0", "uRad1",
         "uColor", "uCam", "uAlpha", "uTime"],
        ["aAng", "aT"]);
      prPool = pack(link(VS_POOL, FS_POOL),
        ["uVP", "uC", "uR", "uF", "uRad", "uColor", "uAlpha"], ["aP"]);
      prMesh = pack(link(VS_MESH, FS_MESH),
        ["uVP", "uModel", "uCam", "uColor", "uEmit", "uSel"],
        ["aPos", "aNrm"]);
      // 32-bit indices.  WebGL 1 needs this for any mesh over 65k vertices,
      // and GDTF models are exported straight out of 3D Studio, so a
      // detailed fixture will cross that line.  If it is missing we fall
      // back to 16-bit indices rather than drawing a corrupted mesh.
      hasUint = !!gl.getExtension("OES_element_index_uint");
      gl.enable(gl.DEPTH_TEST);
      gl.depthFunc(gl.LEQUAL);
      // NO back-face culling, deliberately, and this is the bug that hid the
      // whole feature the first time it ran.
      //
      // The GDTF -> visualiser axis swap is a TRANSPOSITION: exchanging the
      // Y and Z axes.  A transposition is a reflection, determinant -1, and
      // a reflection reverses triangle winding.  So a model whose faces were
      // counter-clockwise when it was authored are clockwise by the time
      // they reach the GPU, and `cullFace(BACK)` with the default
      // `frontFace(CCW)` throws every visible face away.  The draw calls
      // still happen - eight of them, all with valid matrices - so a
      // "how many did we draw" counter cheerfully reported success while
      // the screen showed nothing at all.  Counting draws is not counting
      // pixels, and only looking at the screen finds this.
      //
      // The shader already flips the normal toward the viewer, so the models
      // are lit correctly two-sided, and a fixture is a few hundred triangles
      // where culling saves nothing worth having.
      gl.disable(gl.CULL_FACE);

      // background quad (clip space)
      bufBg = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, bufBg);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
        -1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1,
      ]), gl.STATIC_DRAW);

      // shared taper tube: (cos, sin, t) per vertex
      const SEG = 20;
      const bv = new Float32Array(SEG * 6 * 3);
      let k = 0;
      for (let i = 0; i < SEG; i++) {
        const a0 = (i / SEG) * TAU, a1 = ((i + 1) / SEG) * TAU;
        const c0 = Math.cos(a0), s0 = Math.sin(a0);
        const c1 = Math.cos(a1), s1 = Math.sin(a1);
        bv[k++] = c0; bv[k++] = s0; bv[k++] = 0;
        bv[k++] = c0; bv[k++] = s0; bv[k++] = 1;
        bv[k++] = c1; bv[k++] = s1; bv[k++] = 1;
        bv[k++] = c0; bv[k++] = s0; bv[k++] = 0;
        bv[k++] = c1; bv[k++] = s1; bv[k++] = 1;
        bv[k++] = c1; bv[k++] = s1; bv[k++] = 0;
      }
      BEAM_VERTS = SEG * 6;
      bufBeam = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, bufBeam);
      gl.bufferData(gl.ARRAY_BUFFER, bv, gl.STATIC_DRAW);

      // unit disc for the landing pools
      const SEG2 = 18;
      const pv = new Float32Array(SEG2 * 3 * 2);
      k = 0;
      for (let i = 0; i < SEG2; i++) {
        const a0 = (i / SEG2) * TAU, a1 = ((i + 1) / SEG2) * TAU;
        pv[k++] = 0; pv[k++] = 0;
        pv[k++] = Math.cos(a0); pv[k++] = Math.sin(a0);
        pv[k++] = Math.cos(a1); pv[k++] = Math.sin(a1);
      }
      POOL_VERTS = SEG2 * 3;
      bufPool = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, bufPool);
      gl.bufferData(gl.ARRAY_BUFFER, pv, gl.STATIC_DRAW);

      // main dynamic stream
      bufMain = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, bufMain);
      gl.bufferData(gl.ARRAY_BUFFER, SB.data.byteLength, gl.DYNAMIC_DRAW);
      glCap = SB.data.byteLength;

      gl.disable(gl.DEPTH_TEST);   // painter's order, same as the canvas had
      gl.clearColor(0.016, 0.024, 0.043, 1);
    }

    function setAttrs(pr, specs) {
      for (let i = 0; i < enLocs.length; i++) gl.disableVertexAttribArray(enLocs[i]);
      enLocs.length = 0;
      for (let i = 0; i < specs.length; i++) {
        const s = specs[i];
        gl.enableVertexAttribArray(s[0]);
        gl.vertexAttribPointer(s[0], s[1], gl.FLOAT, false, s[2], s[3]);
        enLocs.push(s[0]);
      }
    }
    function useSolid(blend) {
      if (curProg !== 1) {
        gl.useProgram(prSolid.p);
        gl.uniformMatrix4fv(prSolid.u.uVP, false, vpM);
        curProg = 1;
      }
      if (curBlend !== blend) {
        if (blend) gl.blendFunc(gl.ONE, gl.ONE);
        else gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        curBlend = blend;
      }
      gl.bindBuffer(gl.ARRAY_BUFFER, bufMain);
      setAttrs(prSolid, [
        [prSolid.a.aPos, 3, 28, 0],
        [prSolid.a.aCol, 4, 28, 12],
      ]);
    }
    function useBeam() {
      if (curProg !== 2) {
        gl.useProgram(prBeam.p);
        gl.uniformMatrix4fv(prBeam.u.uVP, false, vpM);
        gl.uniform3f(prBeam.u.uCam, eye[0], eye[1], eye[2]);
        curProg = 2;
      }
      if (curBlend !== 1) { gl.blendFunc(gl.ONE, gl.ONE); curBlend = 1; }
      gl.bindBuffer(gl.ARRAY_BUFFER, bufBeam);
      setAttrs(prBeam, [
        [prBeam.a.aAng, 2, 12, 0],
        [prBeam.a.aT, 1, 12, 8],
      ]);
    }
    function usePool() {
      if (curProg !== 3) {
        gl.useProgram(prPool.p);
        gl.uniformMatrix4fv(prPool.u.uVP, false, vpM);
        curProg = 3;
      }
      if (curBlend !== 1) { gl.blendFunc(gl.ONE, gl.ONE); curBlend = 1; }
      gl.bindBuffer(gl.ARRAY_BUFFER, bufPool);
      setAttrs(prPool, [[prPool.a.aP, 2, 8, 0]]);
    }
    function useMesh() {
      if (curProg !== 4) {
        gl.useProgram(prMesh.p);
        gl.uniformMatrix4fv(prMesh.u.uVP, false, vpM);
        gl.uniform3f(prMesh.u.uCam, eye[0], eye[1], eye[2]);
        curProg = 4;
      }
      curBlend = 0;                 // meshes are opaque, never blended
    }

    /* ------------------------------------------------- the GDTF twin pass
     *
     * Does the GL pass draw this fixture's real model?  One predicate, used
     * by the 2D body pass to decide to stand down.
     *
     * It asks the SCENE, not the manifest, because a definition can have
     * geometry and still have failed to load its mesh - a 404, a truncated
     * download, a format this browser's GL cannot index.  In that case the
     * fixture must fall back to its 2D body, and asking the manifest would
     * say "yes, it has geometry" and leave a hole where the fixture is. */
    function twinHasModel(f) {
      if (!twin || !twinDrawn) return false;
      const hn = f && (f.head_no != null ? f.head_no : f.n);
      if (hn == null) return false;
      const inst = twin.get(hn);
      const has = !!(inst && inst.hasGeometry && inst.hasGeometry());
      // Counted, because "is the 2D body being suppressed" is a question
      // that has been answered wrongly twice by eye.  Both times the answer
      // was believed rather than measured, and both times the operator was
      // looking at a sprite while the numbers said the model was fine.
      if (has) twinSuppressed++;
      return has;
    }

    /* Upload a definition's meshes into GPU buffers, ONCE.
     *
     * This is where "one definition, one load, many instances" becomes real
     * rather than aspirational: the buffers hang off the Definition, so
     * forty heads of one type cost one upload and one set of buffers, and
     * every instance afterwards is just a different uModel.
     *
     * Positions and normals are interleaved into a single buffer so the
     * whole mesh is one binding and one attrib setup.  Indices go into an
     * ELEMENT_ARRAY_BUFFER, which is VAO-free state in WebGL 1 - so the
     * binding has to be repeated after every ARRAY_BUFFER bind, which is
     * why it lives in the draw loop rather than in useMesh(). */
    function uploadTwin(def) {
      if (!def) return null;
      if (!def.gl) def.gl = {};
      // RE-RUNS, and that is the point.  The first version returned early
      // once `def.gl` existed, on the assumption that a definition's meshes
      // are all present the first time it is drawn.  They are not: a
      // definition loads its models in parallel, so a frame can arrive
      // after two of four have landed.  The early return froze the upload at
      // whatever had arrived, and the missing part stayed missing for the
      // life of the view - a real fixture permanently drawn without its
      // head, which reads as a modelling fault rather than a race.
      for (const stem of Object.keys(def.meshes || {})) {
        if (def.gl[stem]) continue;                 // already on the GPU
        const mesh = def.meshes[stem];
        if (!mesh || !mesh.meshes || !mesh.meshes.length) continue;
        let total = 0;
        for (const m of mesh.meshes) total += m.positions.length / 3;
        if (!total) continue;
        const inter = new Float32Array(total * 6);
        let k = 0;
        for (const m of mesh.meshes) {
          const n = m.positions.length;
          for (let i = 0; i < n; i += 3) {
            inter[k++] = m.positions[i];
            inter[k++] = m.positions[i + 1];
            inter[k++] = m.positions[i + 2];
            inter[k++] = m.normals[i] || 0;
            inter[k++] = m.normals[i + 1] || 0;
            inter[k++] = m.normals[i + 2] || 1;
          }
        }
        let idx = null, wide = false;
        for (const m of mesh.meshes) {
          const src = m.indices;
          if (idx) {
            const grow = new (wide ? Uint32Array : Uint16Array)(
              idx.length + src.length);
            grow.set(idx);
            grow.set(src, idx.length);
            idx = grow;
          } else {
            idx = src.slice();
            wide = src instanceof Uint32Array;
          }
        }
        if (!idx) continue;
        // Without 32-bit indices a mesh over 65k vertices cannot be drawn
        // as-is.  Truncating it silently would render half a fixture and
        // look like a modelling error, so the mesh is skipped instead and
        // the definition reports primitives.
        if (wide && !hasUint) {
          def.failed[stem] = "needs 32-bit indices";
          def.failed[stem + "#gpu"] = true;   // do not retry every frame
          continue;
        }
        const vb = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, vb);
        gl.bufferData(gl.ARRAY_BUFFER, inter, gl.STATIC_DRAW);
        const ib = gl.createBuffer();
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
        gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
        def.gl[stem] = { vb: vb, ib: ib, n: idx.length, wide: wide };
      }
      def.refreshState();
      return def.gl;
    }

    function freeTwin(def) {
      if (!def || !def.gl) return;
      for (const stem of Object.keys(def.gl)) {
        const g = def.gl[stem];
        if (g.vb) gl.deleteBuffer(g.vb);
        if (g.ib) gl.deleteBuffer(g.ib);
      }
      def.gl = null;
    }

    /* Draw every instance's real geometry.
     *
     * Depth-tested and opaque, so it runs BEFORE the additive beams, which
     * must stay depth-test-free or every beam would be clipped by the
     * fixture in front of it - including the beam that fixture is
     * throwing, which is the one that matters most. */
    function drawTwin() {
      if (!twin || !prMesh) return;
      let drew = 0;
      // Solve here, on the frame that draws.  The console feeds DMX values
      // in whenever the engine reports them; composing the world matrices
      // here rather than there means a head's transform can never be one
      // frame behind the beam it is throwing, which is the one thing this
      // whole feature must not get wrong.
      twin.update();
      twin.instances.forEach((inst) => {
        const def = inst.def;
        if (!def || !def.nodes.length) { twinSkipped++; return; }
        // Per PART, not per definition: three of four models loading draws
        // those three.  The predicate is `hasGeometry`, which asks whether
        // at least one node has a usable mesh, rather than `fallback`,
        // which is a label about the definition as a whole.
        if (!inst.hasGeometry || !inst.hasGeometry()) { twinSkipped++; return; }
        // Called EVERY frame, not gated on `!def.gl`.  Two mistakes in a row
        // here, both of which leave a fixture permanently missing parts:
        // the guard made uploadTwin a one-shot, so a definition whose models
        // land after the first frame was frozen at whatever had arrived; and
        // then `def.gl` was initialised to `{}`, which is TRUTHY, so the
        // repaired uploadTwin was never actually called again.  12 of 13
        // models were parsed, in memory, and never reached the GPU.
        //
        // The function is idempotent and skips stems already uploaded, so
        // calling it per frame costs one object-key scan per definition.
        uploadTwin(def);
        if (!def.gl || !Object.keys(def.gl).length) return;
        if (!inst.nodeWorld) return;
        const look = inst.dmx || {};
        const hex = look.hex || "#334155";
        const rgb = hexRgb(hex);
        const bright = Math.min(1, (look.a || 0) / 100);
        useMesh();
        const byPath = def.index();
        const draw = (path) => {
          const rec = byPath[path];
          if (!rec) return;
          const g = rec.model && def.gl[rec.model];
          const w = inst.nodeWorld[path];
          if (g && w) {
            gl.uniformMatrix4fv(prMesh.u.uModel, false, w);
            gl.uniform3f(prMesh.u.uColor,
              0.30 + 0.42 * rgb[0], 0.32 + 0.42 * rgb[1], 0.36 + 0.42 * rgb[2]);
            gl.uniform3f(prMesh.u.uEmit,
              rgb[0] * bright, rgb[1] * bright, rgb[2] * bright);
            gl.uniform1f(prMesh.u.uSel, inst.selected ? 1 : 0);
            gl.bindBuffer(gl.ARRAY_BUFFER, g.vb);
            setAttrs(prMesh, [[prMesh.a.aPos, 3, 24, 0],
                              [prMesh.a.aNrm, 3, 24, 12]]);
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, g.ib);
            gl.drawElements(gl.TRIANGLES, g.n,
              g.wide ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT, 0);
            drew++; twinDrew2++;
          }
          for (const c of rec.children) draw(c);
        };
        for (const r of def._roots) draw(r);
      });
      if (drew) { gl.depthMask(true); }
      return drew;
    }

    /* Which head is which, and what state its geometry is in - for the
     * diagnostic surface, so "is the model there" is answerable without
     * a screenshot and without trusting a counter. */
    function twinProbe() {
      const out = [];
      if (!twin) return out;
      twin.instances.forEach((inst) => {
        const d = inst.def;
        const nodes = d ? (d.index ? Object.keys(d.index()) : []) : [];
        const withMesh = d && d.meshes ? Object.keys(d.meshes) : [];
        const onGpu = d && d.gl ? Object.keys(d.gl) : [];
        out.push({
          head: inst.head,
          def: d ? d.id : null,
          state: d ? d.state : null,
          nodes: nodes.length,
          modelsLoaded: withMesh,
          onGpu: onGpu,
          hasGeometry: !!(inst.hasGeometry && inst.hasGeometry()),
          fallback: inst.fallback,
          pos: inst.position,
          dmx: inst.dmx,
          worldNodes: inst.nodeWorld ? Object.keys(inst.nodeWorld).length : 0,
        });
      });
      return out;
    }

    function render() {
      if (!gl || !progs) return;
      curProg = 0; curBlend = -1;
      twinDrawn = 0; twinSuppressed = 0; twinSkipped = 0;
      gl.viewport(0, 0, canvas.width, canvas.height);
      // sky gradient first (opaque)
      gl.disable(gl.BLEND);
      gl.useProgram(prBg.p);
      gl.bindBuffer(gl.ARRAY_BUFFER, bufBg);
      setAttrs(prBg, [[prBg.a.aP, 2, 8, 0]]);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      gl.enable(gl.BLEND);
      // --- GDTF fixture models, opaque and depth-tested, BEFORE the beams
      //
      // The order is the whole trick.  The beams are additive and must NOT
      // be depth-tested, or a fixture's own beam gets clipped by that same
      // fixture's housing.  So the models go down first, with a cleared
      // depth buffer, and everything else carries on exactly as it did -
      // no depth test, additive, on top.  Adding the models at the end, or
      // leaving depth on for the beams, both look plausible and both are
      // wrong.
      gl.clear(gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST);
      gl.depthMask(true);
      gl.disable(gl.BLEND);
      const twinDrew = drawTwin();
      gl.disable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);
      gl.enable(gl.BLEND);
      if (twinDrew) twinDrawn = twinDrew;
      // upload this frame's geometry
      if (SB.n) {
        gl.bindBuffer(gl.ARRAY_BUFFER, bufMain);
        if (SB.data.byteLength > glCap) {
          gl.bufferData(gl.ARRAY_BUFFER, SB.data.byteLength, gl.DYNAMIC_DRAW);
          glCap = SB.data.byteLength;
        }
        gl.bufferSubData(gl.ARRAY_BUFFER, 0, SB.data.subarray(0, SB.n * 7));
      }
      // replay the ops in the old renderer's exact order
      for (let i = 0; i < ops.length; i++) {
        const op = ops[i];
        if (op.k === 0) {
          if (op.c <= 0) continue;
          useSolid(op.b);
          gl.drawArrays(gl.TRIANGLES, op.s, op.c);
        } else if (op.k === 1) {
          const bm = op.bm;
          useBeam();
          const u = prBeam.u;
          gl.uniform3f(u.uO, bm.ox, bm.oy, bm.oz);
          gl.uniform3f(u.uD, bm.dx, bm.dy, bm.dz);
          gl.uniform3f(u.uR, bm.rx, bm.ry, bm.rz);
          gl.uniform3f(u.uF, bm.fx, bm.fy, bm.fz);
          gl.uniform1f(u.uLen, bm.len);
          gl.uniform1f(u.uRad0, bm.rad0);
          gl.uniform1f(u.uRad1, bm.rad1);
          gl.uniform3f(u.uColor, bm.cr, bm.cg, bm.cb);
          gl.uniform1f(u.uAlpha, bm.a);
          gl.uniform1f(u.uTime, timeNow);
          gl.drawArrays(gl.TRIANGLES, 0, BEAM_VERTS);
        } else {
          const pm = op.pm;
          usePool();
          const u = prPool.u;
          gl.uniform3f(u.uC, pm.cx, pm.cy, pm.cz);
          gl.uniform3f(u.uR, 1, 0, 0);
          gl.uniform3f(u.uF, 0, 0, 1);
          gl.uniform1f(u.uRad, pm.rad);
          gl.uniform3f(u.uColor, pm.cr, pm.cg, pm.cb);
          gl.uniform1f(u.uAlpha, pm.a);
          gl.drawArrays(gl.TRIANGLES, 0, POOL_VERTS);
        }
      }
    }

    /* ------------------------------------------------ head-number labels */
    // What the operator has selected, drawn on the overlay.
    //
    // It has to live on the LABEL canvas rather than in the scene geometry:
    // the scene goes through WebGL, where a selection ring would mean a
    // whole extra line-drawing pass, while the overlay is 2D, sits on top of
    // every render path (GL, poster, no-GL) and can put readable text beside
    // the light.  It used to be drawn in the poster FALLBACK only, so on any
    // machine with WebGL - which is every machine - the ring the code
    // appeared to draw was never reached.  And nothing ever set the flag it
    // tested, so even the fallback showed nothing.  Two independent reasons
    // the highlight could not appear, and it looked finished the whole time.
    function drawSelection() {
      const list = stage.fixtures || [];
      for (const f of list) {
        if (!f.sel && !f.picked) continue;
        const p = project(f.x, f.y, f.z);
        if (!p) continue;
        // just picked (f.picked) reads hotter than merely selected
        const hot = !!f.picked;
        const r = Math.max(10, Math.min(46, p.s * 0.46));
        lctx.save();
        // dashed ring: the state is not carried by colour alone
        lctx.strokeStyle = hot ? "rgba(34,211,238,0.95)"
                               : "rgba(34,211,238,0.6)";
        lctx.lineWidth = 2;
        if (lctx.setLineDash) lctx.setLineDash([5, 4]);
        lctx.beginPath();
        lctx.arc(p.x, p.y, r, 0, TAU);
        lctx.stroke();
        if (lctx.setLineDash) lctx.setLineDash([]);
        // corner ticks - a reticle, the way a program marks its current
        // object: readable at any size and in greyscale
        lctx.lineWidth = 2.5;
        for (let q = 0; q < 4; q++) {
          const a = Math.PI / 4 + q * (Math.PI / 2);
          const cx = Math.cos(a), cy = Math.sin(a);
          lctx.beginPath();
          lctx.moveTo(p.x + cx * r * 0.72, p.y + cy * r * 0.72);
          lctx.lineTo(p.x + cx * r * 1.14, p.y + cy * r * 1.14);
          lctx.stroke();
        }
        lctx.restore();

        // A chip naming the light, so "click it so I can edit it" answers
        // the next question without a trip to the patch list.
        const name = f.model || f.name || "";
        const text = "#" + f.head_no + (name ? "  " + name : "");
        lctx.font = "600 11px Consolas, monospace";
        const tw = lctx.measureText(text).width;
        const bw = Math.max(24, Math.min(W - 8, tw + 14));
        const bh = 18;
        const bx = Math.max(4, Math.min(W - bw - 4, p.x - bw / 2));
        const by = Math.max(2, p.y - r - bh - 5);
        lctx.fillStyle = "rgba(6,12,20,0.92)";
        lctx.strokeStyle = hot ? "rgba(34,211,238,0.95)"
                               : "rgba(34,211,238,0.7)";
        lctx.lineWidth = 1;
        lctx.beginPath();
        if (lctx.roundRect) lctx.roundRect(bx, by, bw, bh, 4);
        else lctx.rect(bx, by, bw, bh);
        lctx.fill();
        lctx.stroke();
        lctx.fillStyle = "#dbeafe";
        lctx.textAlign = "center";
        lctx.fillText(text, bx + bw / 2, by + 13, bw - 10);
      }
    }

    function drawLabels(clear) {
      if (!lctx) return;
      lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (clear) lctx.clearRect(0, 0, W, H);
      lctx.textAlign = "center";
      lctx.fillStyle = "rgba(148,163,184,0.85)";
      (stage.fixtures || []).forEach((f) => {
        const p = project(f.x, f.y, f.z);
        if (!p) return;
        const size = clamp(p.s * 0.09, 4, 12);
        lctx.font = clamp(size * 0.8, 9, 12) + "px Consolas, monospace";
        lctx.fillText(String(f.head_no), p.x, p.y - size * 1.25 * 0.7 - 3);
      });
      // over the numbers, so a selected light is never hidden by them
      drawSelection();
    }

    /* --------------------------------------------- 2D fallback poster */
    function drawPoster(c) {
      if (!c || !W || !H) return;
      const sky = c.createLinearGradient(0, 0, 0, H);
      sky.addColorStop(0, "#04060b");
      sky.addColorStop(0.6, "#060911");
      sky.addColorStop(1, "#080c15");
      c.fillStyle = sky;
      c.fillRect(0, 0, W, H);

      const R = roomBox();
      const hw = stage.width / 2, d = stage.depth;
      const ry = roofY();

      function poly(pts, fill, stroke, lw) {
        const ps = [];
        for (let i = 0; i < pts.length; i++) {
          const q = project(pts[i][0], pts[i][1], pts[i][2]);
          if (!q) return;
          ps.push(q);
        }
        c.beginPath();
        c.moveTo(ps[0].x, ps[0].y);
        for (let i = 1; i < ps.length; i++) c.lineTo(ps[i].x, ps[i].y);
        c.closePath();
        if (fill) { c.fillStyle = fill; c.fill(); }
        if (stroke) { c.strokeStyle = stroke; c.lineWidth = lw || 1; c.stroke(); }
      }
      function seg(a, b, stroke, lw) {
        const pa = project(a[0], a[1], a[2]), pb = project(b[0], b[1], b[2]);
        if (!pa || !pb) return;
        c.strokeStyle = stroke; c.lineWidth = lw || 1;
        c.beginPath(); c.moveTo(pa.x, pa.y); c.lineTo(pb.x, pb.y); c.stroke();
      }
      function sAt(x, y, z) { const p = project(x, y, z); return p ? p.s : 8; }

      // The default stage room.  Skipped when the operator drew a venue -
      // the venue block below draws the same box from their dimensions,
      // and drawing both stacks two translucent rooms.
      if (!hasVenue()) {
        poly([[R.x0, 0, R.z0], [R.x1, 0, R.z0],
              [R.x1, R.y1, R.z0], [R.x0, R.y1, R.z0]],
             "rgba(15, 23, 40, 0.8)", "#1d2a44", 1.2);
        const rc = [[R.x0, R.y1, R.z0], [R.x1, R.y1, R.z0],
                    [R.x1, R.y1, R.z1], [R.x0, R.y1, R.z1]];
        for (let i = 0; i < 4; i++) {
          seg(rc[i], rc[(i + 1) % 4], "rgba(112, 126, 156, 0.4)", 1.2);
        }
        seg([R.x0, 0, R.z0], [R.x0, R.y1, R.z0], "rgba(88, 102, 130, 0.5)", 1.2);
        seg([R.x1, 0, R.z0], [R.x1, R.y1, R.z0], "rgba(88, 102, 130, 0.5)", 1.2);
      }

      /* ---------------------------------------------------------- venue
       *
       * The room is what the operator drew.  With nothing stored we show
       * an empty stage (an open floor with no walls) rather than inventing
       * a venue: "be creative" starts from a blank canvas, and every
       * surface below is driven by opts.venue.
       *
       * opts.venue = {width_m, depth_m, height_m, name,
       *               surfaces: [{kind, ...}], units, notes}
       *   kind: "floor" | "wall" | "ceiling" | "truss" | "box"
       * Anything the operator draws goes through the same path as the
       * rig, so venue and lights share one coordinate system. */
      const V = opts.venue || {};
      const vW = Number(V.width_m) || 0;
      const vD = Number(V.depth_m) || 0;
      const vH = Number(V.height_m) || 0;
      if (vW > 0 && vD > 0) {
        // Back wall first, so the floor plate and the truss read in front
        // of it.  This is the venue's own wall (open at the front, where
        // the audience is), not the default stage box above.
        poly([[-vW / 2, 0, vD], [vW / 2, 0, vD],
              [vW / 2, vH || roofY(), vD], [-vW / 2, vH || roofY(), vD]],
             "rgba(15, 23, 40, 0.8)", "#1d2a44", 1.2);
        // floor plate, drawn as a grid so scale is readable
        c.save();
        c.beginPath();
        const corner = [[-vW / 2, 0, 0], [vW / 2, 0, 0],
                        [vW / 2, 0, vD], [-vW / 2, 0, vD]];
        corner.forEach((p, i) => {
          const q = project(p[0], p[1], p[2]);
          if (!q) return;
          if (i === 0) c.moveTo(q.x, q.y); else c.lineTo(q.x, q.y);
        });
        c.closePath();
        c.fillStyle = "rgba(30, 41, 59, 0.55)";
        c.fill();
        c.strokeStyle = "rgba(148, 163, 184, 0.55)";
        c.lineWidth = 1.4;
        c.stroke();
        c.restore();
        // 1 m grid
        c.strokeStyle = "rgba(148, 163, 184, 0.14)";
        c.lineWidth = 1;
        c.beginPath();
        for (let x = -Math.floor(vW / 2); x <= vW / 2; x += 1) {
          const a = project(x, 0.01, 0), b = project(x, 0.01, vD);
          if (a && b) { c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); }
        }
        for (let z = 0; z <= vD; z += 1) {
          const a = project(-vW / 2, 0.01, z), b = project(vW / 2, 0.01, z);
          if (a && b) { c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); }
        }
        c.stroke();
        if (vH > 0) {
          // room walls: back + two sides, open at the front (audience)
          const walls = [
            [[-vW / 2, 0, 0], [-vW / 2, vH, 0]],
            [[vW / 2, 0, 0], [vW / 2, vH, 0]],
            [[-vW / 2, 0, vD], [-vW / 2, vH, vD]],
            [[vW / 2, 0, vD], [vW / 2, vH, vD]],
            [[-vW / 2, vH, vD], [vW / 2, vH, vD]],
            [[-vW / 2, vH, 0], [vW / 2, vH, 0]],
          ];
          walls.forEach((w) => {
            seg(w[0], w[1], "rgba(148, 163, 184, 0.28)", 1.2);
          });
        }
      }
      (V.surfaces || []).forEach((sf) => {
        const k = String(sf.kind || "box");
        const col = sf.color || "#64748b";
        const a = [Number(sf.x1), Number(sf.y1), Number(sf.z1)];
        const b = [Number(sf.x2), Number(sf.y2), Number(sf.z2)];
        if (a.some(isNaN) || b.some(isNaN)) return;
        if (k === "truss") {
          seg(a, b, col, clamp(sAt(a[0], a[1], a[2]) * 0.05, 1.5, 4));
        } else if (k === "wall" || k === "ceiling") {
          seg(a, b, col, 1.4);
        } else {
          seg(a, b, col, 1.4);
          seg(b, a, col, 1.4);
        }
      });

      c.lineWidth = 1;
      c.strokeStyle = "#0e1626";
      c.beginPath();
      for (let x = Math.ceil(R.x0); x <= R.x1; x += 1) {
        const a = project(x, 0, R.z0), b = project(x, 0, R.z1);
        if (!a || !b) continue;
        c.moveTo(a.x, a.y); c.lineTo(b.x, b.y);
      }
      for (let z = Math.ceil(R.z0); z <= R.z1; z += 1) {
        const a = project(R.x0, 0, z), b = project(R.x1, 0, z);
        if (!a || !b) continue;
        c.moveTo(a.x, a.y); c.lineTo(b.x, b.y);
      }
      c.stroke();
      poly([[-hw, 0, 0], [hw, 0, 0], [hw, 0, d], [-hw, 0, d]],
           "rgba(17, 27, 48, 0.68)", "#2f4a74", 1.6);

      // rig
      trussRows().forEach((r) => {
        setColor("#4d5871", 1);
        seg([r.x0, ry, r.z0], [r.x0, r.barY + 0.22, r.z0], "#4d5871",
            Math.max(1, clamp(sAt(r.x0, ry, r.z0) * 0.035, 1, 3)));
        seg([r.x1, ry, r.z1], [r.x1, r.barY + 0.22, r.z1], "#4d5871",
            Math.max(1, clamp(sAt(r.x1, ry, r.z1) * 0.035, 1, 3)));
        const hh = 0.22;
        const at = (t, y) => [
          r.x0 + (r.x1 - r.x0) * t, y, r.z0 + (r.z1 - r.z0) * t,
        ];
        const t0 = at(0, r.barY + hh), t1 = at(1, r.barY + hh);
        const b0 = at(0, r.barY - hh), b1 = at(1, r.barY - hh);
        const pa = project(t0[0], t0[1], t0[2]), pb = project(t1[0], t1[1], t1[2]);
        if (!pa || !pb) return;
        const px = Math.hypot(pa.x - pb.x, pa.y - pb.y);
        const n = Math.round(Math.hypot(r.x1 - r.x0, r.z1 - r.z0) / 0.75);
        if (px > 64 && n >= 2) {
          const w2 = Math.max(1, clamp(pa.s * 0.03, 1, 2));
          for (let i = 0; i <= n; i++) {
            seg(at(i / n, r.barY - hh), at(i / n, r.barY + hh), "#4a566f", w2);
          }
        }
        const ow = clamp(pa.s * 0.13, 2.5, 10);
        const iw = Math.max(1, clamp(pa.s * 0.045, 1, 3));
        seg(t0, t1, "#39445a", ow);
        seg(t0, t1, "#5d6a85", iw);
        seg(b0, b1, "#39445a", ow);
        seg(b0, b1, "#5d6a85", iw);
      });

      // beams (same maths as the original canvas renderer)
      c.save();
      c.globalCompositeOperation = "lighter";
      beamList.forEach((bm) => {
        const look = bm.look;
        if (!look || look.a <= 0.02) return;
        const meta = bm.meta;
        const oP = project(bm.ox, bm.oy, bm.oz), eP = project(bm.ex, bm.ey, bm.ez);
        if (!oP || !eP) return;
        const ol = project(bm.ox - 0.05, bm.oy, bm.oz);
        const orr = project(bm.ox + 0.05, bm.oy, bm.oz);
        const el = project(bm.ex - bm.halfW, bm.ey, bm.ez);
        const er = project(bm.ex + bm.halfW, bm.ey, bm.ez);
        if (!ol || !orr || !el || !er) return;
        const alpha = look.a * 0.6 * meta.glow;
        const grad = c.createLinearGradient(oP.x, oP.y, eP.x, eP.y);
        grad.addColorStop(0, rgba(look.hex, alpha * 0.8));
        grad.addColorStop(0.45, rgba(look.hex, alpha * 0.35));
        grad.addColorStop(1, rgba(look.hex, alpha * 0.05));
        c.fillStyle = grad;
        c.beginPath();
        c.moveTo(ol.x, ol.y);
        c.lineTo(orr.x, orr.y);
        c.lineTo(er.x, er.y);
        c.lineTo(el.x, el.y);
        c.closePath();
        c.fill();
        c.fillStyle = rgba(look.hex, alpha * 0.45);
        const cl = project(bm.ox - 0.02, bm.oy, bm.oz);
        const crn = project(bm.ox + 0.02, bm.oy, bm.oz);
        const cel = project(bm.ex - bm.halfW * 0.3, bm.ey, bm.ez);
        const cer = project(bm.ex + bm.halfW * 0.3, bm.ey, bm.ez);
        if (cl && crn && cel && cer) {
          c.beginPath();
          c.moveTo(cl.x, cl.y);
          c.lineTo(crn.x, crn.y);
          c.lineTo(cer.x, cer.y);
          c.lineTo(cel.x, cel.y);
          c.closePath();
          c.fill();
        }
        const pr = Math.max(6, bm.halfW * eP.s * 1.1);
        const pool = c.createRadialGradient(eP.x, eP.y, 0, eP.x, eP.y, pr);
        pool.addColorStop(0, rgba(look.hex, alpha * (bm.hang ? 0.55 : 0.4)));
        pool.addColorStop(1, rgba(look.hex, 0));
        c.fillStyle = pool;
        c.beginPath();
        c.ellipse(eP.x, eP.y, pr, pr * (bm.hang ? 0.38 : 0.30), 0, 0, TAU);
        c.fill();
      });
      c.restore();

      // fixture bodies: brand/geometry shaped, drawn in the projected
      // frame so they stay put while the camera orbits
      (stage.fixtures || []).forEach((f, i) => {
        const bm = beamList[i];
        const look = bm ? bm.look : (f._look || cur[f.role]);
        const on = look && look.a > 0.02;
        const col = on ? look.hex : (ROLE_OFF[f.role] || ROLE_OFF.generic);
        // If the twin drew this head's real model in the GL pass, drawing a
        // 2D sprite on top of it as well would show two fixtures occupying
        // one hang point - a box inside a moving head.  The 2D body is the
        // FALLBACK, so this is the fallback decision, made in one place.
        if (twinHasModel(f)) return;
        const spec = bodyFor(f);
        // hang point: on a bar, from the roof, or standing on the deck
        let ax, ay, az;
        if (onBar(f)) { ax = f.x; ay = f.y + 0.23; az = f.z; }
        else if (hanging(f)) { ax = f.x; ay = ry; az = f.z; }
        else { ax = f.x; ay = 0; az = f.z; }
        seg([ax, ay, az], [f.x, f.y, f.z], "#4b5a75",
            Math.max(1, project(f.x, f.y, f.z).s * 0.022));
        const p = project(f.x, f.y, f.z);
        if (!p) return;
        const u = clamp(p.s * 0.06, 3, 11);      // px per body unit
        const lit = on ? rgba(col, 0.9) : "#334155";
        const face = on ? rgba(col, 0.95) : rgba(col, 0.5);
        c.lineWidth = 1.4;
        const box = (dx, dy, w, h, fill) => {
          c.fillStyle = fill; c.strokeStyle = lit;
          c.fillRect(p.x + dx * u - (w * u) / 2, p.y + dy * u - (h * u) / 2,
            w * u, h * u);
          c.strokeRect(p.x + dx * u - (w * u) / 2, p.y + dy * u - (h * u) / 2,
            w * u, h * u);
        };
        const disc = (dx, dy, r, fill) => {
          c.fillStyle = fill; c.strokeStyle = lit;
          c.beginPath();
          c.arc(p.x + dx * u, p.y + dy * u, Math.max(1.4, r * u), 0, TAU);
          c.fill(); c.stroke();
        };
        switch (spec.shape) {
          case "yoke":
            // base clamp, two yoke arms, a head between them
            box(0, 0.85, 0.5, 0.22, "#111a2e");
            c.strokeStyle = lit; c.lineWidth = Math.max(1, u * 0.09);
            c.beginPath();
            c.moveTo(p.x - 0.26 * u, p.y + 0.78 * u);
            c.lineTo(p.x - 0.26 * u, p.y - 0.12 * u);
            c.lineTo(p.x + 0.26 * u, p.y - 0.12 * u);
            c.lineTo(p.x + 0.26 * u, p.y + 0.78 * u);
            c.stroke();
            // the head, tilted downstage the way a spot points
            c.save();
            c.translate(p.x, p.y - 0.02 * u);
            c.rotate(bm ? Math.max(-0.9, Math.min(0.9, (bm.pan || 0) * 0.2)) : 0);
            c.fillStyle = "#0d1526";
            c.fillRect(-0.19 * u, -0.19 * u, 0.38 * u, 0.38 * u);
            c.strokeStyle = lit; c.lineWidth = 1.3;
            c.strokeRect(-0.19 * u, -0.19 * u, 0.38 * u, 0.38 * u);
            // lens
            c.fillStyle = face;
            c.beginPath();
            c.ellipse(0, 0.14 * u, 0.11 * u, 0.11 * u, 0, 0, TAU);
            c.fill();
            c.restore();
            break;
          case "tube":
            // batten: a long thin body, ends marked
            c.fillStyle = "#0d1526"; c.strokeStyle = lit;
            c.fillRect(p.x - 1.1 * u, p.y - 0.11 * u, 2.2 * u, 0.22 * u);
            c.strokeRect(p.x - 1.1 * u, p.y - 0.11 * u, 2.2 * u, 0.22 * u);
            for (let k = 0; k < 3; k++) {
              disc(-0.72 * u + k * 0.72 * u, 0, 0.08, face);
            }
            break;
          case "panel":
            // wash panel: a wide flat face
            c.fillStyle = "#0d1526"; c.strokeStyle = lit;
            c.fillRect(p.x - 0.85 * u, p.y - 0.2 * u, 1.7 * u, 0.4 * u);
            c.strokeRect(p.x - 0.85 * u, p.y - 0.2 * u, 1.7 * u, 0.4 * u);
            c.fillStyle = face;
            for (let k = 0; k < 4; k++) {
              c.fillRect(p.x - 0.78 * u + k * 0.42 * u, p.y - 0.13 * u,
                0.3 * u, 0.26 * u);
            }
            break;
          case "can":
          default:
            // PAR can: a short body with one big lens
            box(0, 0, 0.62, 0.44, "#0d1526");
            disc(0, 0.13 * u, 0.17, face);
            break;
        }
        // selection ring - drawn on the overlay too (drawSelection), but
        // the poster is a separate 2D pass so it needs its own copy
        if (f.sel || f.picked) {
          c.strokeStyle = "#22d3ee";
          c.lineWidth = 2;
          c.beginPath();
          c.arc(p.x, p.y, Math.max(9, u * 1.5), 0, TAU);
          c.stroke();
        }
      });
    }

    /* ----------------------------------------------------------- render */
    function draw() {
      if (destroyed || !W || !H) return;
      rowsMemo = null;
      roofMemo = null;
      updateCamera();
      timeNow = performance.now() / 1000;
      buildScene();
      if (gl && progs) {
        render();
        drawLabels(true);
      } else if (gl && posterCtx) {
        // shader setup failed after the context was made: clear GL, poster
        // rides on the overlay (a canvas cannot switch context type)
        gl.clear(gl.COLOR_BUFFER_BIT);
        drawPoster(posterCtx);
        drawLabels(false);
      } else if (gl) {
        gl.clear(gl.COLOR_BUFFER_BIT);
        drawLabels(true);
      } else {
        drawPoster(posterCtx);
        drawLabels(true);
      }
    }

    function tick(now) {
      if (destroyed) return;
      const fading = fadeMs > 0 && t0;
      if (fading) {
        const u = Math.min(1, (now - t0) / fadeMs);
        const e = smooth(u);
        roles.forEach((r) => {
          const f = from[r] || to[r], t = to[r] || from[r];
          if (!f || !t) return;
          cur[r] = { hex: mixHex(f.hex, t.hex, e), a: lerp(f.a, t.a, e) };
        });
        if (u >= 1) t0 = 0;
      }
      // live mode: ease every head toward the values the engine reported
      const liveEasing = liveCur && liveT0;
      if (liveEasing) {
        const u = liveDur > 0 ? Math.min(1, (now - liveT0) / liveDur) : 1;
        const e = smooth(u);
        (stage.fixtures || []).forEach((f) => {
          const k = String(f.head_no);
          const want = liveCur[k];
          const prev = liveFrom[k] || want;
          if (!want) { f._look = { hex: "#f4f7ff", a: 0 }; return; }
          f._look = {
            hex: mixHex(prev.hex, want.hex, e),
            a: Math.max(0, Math.min(1, lerp(prev.a, want.a, e))),
          };
          // Aim is carried, not eased.  Colour and level are fades the
          // client smooths between 20 Hz feed ticks, but pan/tilt arrive
          // ALREADY interpolated - the engine eases them at output rate
          // and the feed samples that, so a sweep arrives as 20 correct
          // intermediate positions a second.  Easing them again here only
          // added a second writer racing syncAim for the same object, and
          // it lost: the aim vanished, and the head sat still while the
          // desk pointed it elsewhere.  Absent means not driven, and the
          // key is deleted so beamOf falls back to its geometric landing.
          if (typeof want.pan === "number") f._look.pan = want.pan;
          else delete f._look.pan;
          if (typeof want.tilt === "number") f._look.tilt = want.tilt;
          else delete f._look.tilt;
        });
        if (u >= 1) liveT0 = 0;
      }
      // Camera work is continuous, so it lives here rather than in the key
      // event: the key just says "held", this says how far this frame got.
      // Without a dt guard a tab restored from the background would jump the
      // camera metres in one frame.
      const dt = lastFrameT ? Math.min(0.1, (now - lastFrameT) / 1000) : 0.016;
      lastFrameT = now;
      const camBusy = applyHeldKeys(dt) || stepFly(now);
      draw();
      if (opts.still && !playing && !fading && !liveEasing && !camBusy
          && !fly && !anyKeyHeld()) {
        raf = 0;
        return;
      }
      raf = requestAnimationFrame(tick);
    }

    function anyKeyHeld() {
      for (const k in keys) if (keys[k]) return true;
      return false;
    }

    function ensureLoop() {
      if (!raf && !destroyed) raf = requestAnimationFrame(tick);
    }

    // Put the aim on the fixtures NOW, synchronously, rather than waiting
    // for an animation frame to ease it there.
    //
    // Colour and level are fades, so easing them is right.  Aim is not a
    // fade - it is a position, and the head is either pointed at something
    // or it is not.  Tying it to the easing loop made the geometry depend
    // on requestAnimationFrame actually firing: in a backgrounded tab, or
    // any tab the browser decides not to paint, the beam stayed wherever
    // the last painted frame left it while the desk reported a different
    // aim.  Measured - the feed said pan 0.25 and the beam did not move.
    // Easing still runs on top of this wherever frames are available, so
    // a pan sweep is smooth; this is the floor under it, not a
    // replacement for it.
    function syncAim() {
      (stage.fixtures || []).forEach((f) => {
        const want = liveCur ? liveCur[String(f.head_no)] : null;
        if (!f._look) f._look = { hex: "#f4f7ff", a: 0 };
        if (!want) {
          // no longer driven: drop the aim so beamOf falls back to its
          // geometric landing rather than freezing on the last one
          delete f._look.pan;
          delete f._look.tilt;
          return;
        }
        if (typeof want.pan === "number") f._look.pan = want.pan;
        else delete f._look.pan;
        if (typeof want.tilt === "number") f._look.tilt = want.tilt;
        else delete f._look.tilt;
      });
    }

    // still-frame instances (layout cards, editor) redraw on demand
    function poke() {
      if (opts.still && !(fadeMs > 0 && t0)) draw();
    }

    /* ------------------------------------------------- orbit controls
     *
     * Three interactions share one canvas:
     *   drag empty space  = orbit the camera (as before)
     *   drag a fixture    = move that fixture on the stage plane
     *   click a fixture   = report it to the caller (patch highlight)
     *
     * A fixture is picked by screen-space distance, so it works at any
     * camera angle; holding Shift (or Alt) moves it vertically instead,
     * which is how you get a floor light up onto the truss.
     */
    let dragging = false, lastX = 0, lastY = 0, dragCam = false;
    let grabbed = null, grabMode = null, grabOff = [0, 0], grabHead = 0;
    let moved = false;

    // Pointer position in CANVAS-LOCAL pixels.
    //
    // project() and screenToPlane() both work in the canvas's own 0..W / 0..H
    // space, but pointer events report clientX/clientY in VIEWPORT space.
    // The console's canvas sits at roughly (459, 108), so comparing the
    // two directly is off by that offset and picking silently finds
    // nothing unless the canvas happens to be at the viewport origin.
    // The offset is cached from resize() rather than read per event.
    let offX = 0, offY = 0;
    function cacheOffset() {
      const r = canvas.getBoundingClientRect();
      offX = r.left;
      offY = r.top;
    }
    function localPoint(e) {
      return [e.clientX - offX, e.clientY - offY];
    }

    // The world point under the cursor where the cursor ray meets a plane.
    //
    // Two planes matter when moving a fixture:
    //   horizontal (y = const) - slide a light across the floor or along
    //     the truss.  The plane is the fixture's OWN height, so a light
    //     on the truss stays on the truss instead of dropping to the deck
    //     the moment you nudge it sideways.
    //   vertical (facing the camera) - change its height.  A horizontal
    //     plane cannot express this: moving the cursor up the screen on a
    //     horizontal plane slides the light in depth, not in height.
    //
    // `vertical` selects the second; `planeY` is the height of the first.
    function screenToPlane(px, py, planeY, vertical, through) {
      const nx = (px / W) * 2 - 1;
      const ny = 1 - (py / H) * 2;
      const inv = m4();
      if (!m4Invert(inv, vpM)) return null;
      const A = tr(inv, nx, ny, -1), B = tr(inv, nx, ny, 1);
      const aw = A[3] || 1e-6, bw = B[3] || 1e-6;
      const ax = A[0] / aw, ay = A[1] / aw, az = A[2] / aw;
      const bx = B[0] / bw, by = B[1] / bw, bz = B[2] / bw;
      let t;
      if (vertical) {
        // plane through `through` with the camera's horizontal facing as
        // its normal, so dragging up/down is unambiguously height
        const n = [fwd[0], 0, fwd[2]];
        const len = Math.hypot(n[0], n[2]);
        if (len < 1e-6) return null;             // looking straight down
        n[0] /= len; n[2] /= len;
        const denom = (bx - ax) * n[0] + (bz - az) * n[2];
        if (Math.abs(denom) < 1e-6) return null;  // ray parallel to plane
        t = ((through[0] - ax) * n[0] + (through[2] - az) * n[2]) / denom;
      } else {
        const dy = by - ay;
        if (Math.abs(dy) < 1e-6) return null;
        t = (planeY - ay) / dy;
      }
      if (t < 0) t = 0;                          // clamp behind the camera
      return [ax + (bx - ax) * t, ay + (by - ay) * t, az + (bz - az) * t];
    }

    // nearest fixture to a screen point, within a 26 px radius
    function fixtureAt(px, py) {
      const list = stage.fixtures || [];
      let best = null, bestD = 26;
      for (let i = 0; i < list.length; i++) {
        const f = list[i];
        const p = project(f.x || 0, f.y || 0, f.z || 0);
        if (!p) continue;
        const d = Math.hypot(p.x - px, p.y - py);
        if (d < bestD) { bestD = d; best = f; }
      }
      return best;
    }

    /* -------------------------------------------------- camera movement */
    // The camera's own axes, so "left" and "up" mean what they look like on
    // screen no matter which way the view is facing.  This is the difference
    // between an orbit toy and something you can actually fly a rig in.
    function camRight(out) {
      // right = forward x worldUp, then re-orthogonalised against forward
      let rx = fwd[2] * 1 - fwd[1] * 0;
      let ry = 0;
      let rz = 0 - fwd[0] * 1;
      const rl = Math.hypot(rx, ry, rz);
      if (rl < 1e-5) { rx = 1; ry = 0; rz = 0; }
      else { rx /= rl; rz /= rl; }
      out[0] = rx; out[1] = ry; out[2] = rz;
      return out;
    }
    function camUp(out) {
      // up = forward x right.
      //
      // The order matters and getting it backwards is invisible in the
      // maths and obvious in the room: right x forward points DOWN, so the
      // Q/E keys flew the camera into the floor while A/D and W/S all felt
      // correct.  A bug that only shows on one axis is the kind that ships.
      const r = camRight(_ax);
      out[0] = fwd[1] * r[2] - fwd[2] * r[1];
      out[1] = fwd[2] * r[0] - fwd[0] * r[2];
      out[2] = fwd[0] * r[1] - fwd[1] * r[0];
      const ul = Math.hypot(out[0], out[1], out[2]);
      if (ul < 1e-4) {
        // Looking straight up or down: the cross product is degenerate and
        // there is no meaningful screen-up.  Fall back to world up so the
        // key still lifts the camera instead of doing nothing.
        out[0] = 0; out[1] = 1; out[2] = 0;
        return out;
      }
      out[0] /= ul; out[1] /= ul; out[2] /= ul;
      return out;
    }
    const _ax = [0, 0, 0];

    // Slide the target in the view plane, by exactly as much world units as
    // the pointer travelled in screen units at the target's depth.  That is
    // what makes the room follow the cursor 1:1 instead of drifting.
    function panBy(dxPx, dyPx) {
      const k = dist / focal;             // world units per pixel at tgt
      const r = camRight(_ax);
      const u = camUp(_ay);
      tgt[0] += (-r[0] * dxPx + u[0] * dyPx) * k;
      tgt[1] += (-r[1] * dxPx + u[1] * dyPx) * k;
      tgt[2] += (-r[2] * dxPx + u[2] * dyPx) * k;
      camTouched = true;
    }
    const _ay = [0, 0, 0];

    // Dolly toward whatever is under the cursor, Unity-style: the point
    // under the pointer stays under the pointer while the camera closes in.
    // Without this, zooming at a rig only ever rushes it at the screen
    // centre, which is the least useful place on a lighting plot.
    function dollyTo(factor, px, py) {
      const before = (px === undefined) ? null : screenToPlane(px, py,
                                                                tgt[1], false, tgt);
      dist = clamp(dist * factor, baseDist() * 0.08, baseDist() * 6);
      if (before) {
        // camera moved, so re-derive the same ray in the new camera and
        // shift the target so it lands where it did
        const after = screenToPlane(px, py, tgt[1], false, tgt);
        if (after) {
          tgt[0] += before[0] - after[0];
          tgt[1] += before[1] - after[1];
          tgt[2] += before[2] - after[2];
        }
      }
    }

    // Free-fly one frame's worth of WASD/QE, scaled by the current dolly so
    // the same key press travels the same apparent distance at any zoom.
    function flyStep(fwdAmt, rightAmt, upAmt, dt) {
      const speed = Math.max(0.6, dist * 0.9) * (dt || 0.016);
      const r = camRight(_ax);
      const u = camUp(_ay);
      for (let i = 0; i < 3; i++) {
        tgt[i] += (fwd[i] * fwdAmt + r[i] * rightAmt + u[i] * upAmt) * speed;
      }
    }

    function onDown(e) {
      dragging = true;
      moved = false;
      lastX = e.clientX;
      lastY = e.clientY;
      cacheOffset();
      // The fly keys are bound to the canvas, so the canvas has to hold
      // focus for WASD to reach it.  A canvas is focusable (tabIndex below)
      // but a click is not guaranteed to land on it once pointer capture
      // and preventDefault are in play, so ask for it explicitly.
      if (typeof canvas.focus === "function") {
        try { canvas.focus({ preventScroll: true }); } catch (x) {
          try { canvas.focus(); } catch (y) { /* ignore */ }
        }
      }
      const [mx, my] = localPoint(e);
      // Camera pan is a right-button or middle-button drag, or Shift with
      // the left.  Left on empty space still orbits, so the gesture an
      // operator already has is not taken away.
      const panDrag = (e.button === 1) || (e.button === 2)
                      || (e.button === 0 && (e.shiftKey || e.altKey)
                          && !opts.editable);
      dragCam = panDrag || (e.button === 2);
      // On a touch device two fingers = pan + zoom, matching every 3D app.
      if (panDrag || e.button === 1 || e.button === 2) {
        canvas.style.cursor = "grabbing";
        if (canvas.setPointerCapture) {
          try { canvas.setPointerCapture(e.pointerId); } catch (x) { /* ignore */ }
        }
        return;
      }
      const hit = opts.editable ? fixtureAt(mx, my) : null;
      if (hit) {
        grabbed = hit;
        grabHead = hit.head_no;
        grabMode = (e.shiftKey || e.altKey) ? "y" : "xz";
        // Slide across the fixture's OWN height; shift/alt works on a
        // vertical plane through it so up/down means up/down.
        const planeY = hit.y || 0.3;
        const w = screenToPlane(mx, my, planeY, grabMode === "y",
                                [hit.x || 0, planeY, hit.z || 0]);
        grabOff = w
          ? [(hit.x || 0) - w[0], (hit.y || 0.3) - w[1], (hit.z || 0) - w[2]]
          : [0, 0, 0];
        canvas.style.cursor = "grabbing";
      }
      if (canvas.setPointerCapture) {
        try { canvas.setPointerCapture(e.pointerId); } catch (x) { /* ignore */ }
      }
    }
    function onMove(e) {
      if (!dragging) {
        if (opts.editable) {
          const [hx, hy] = localPoint(e);
          canvas.style.cursor = fixtureAt(hx, hy) ? "move" : "";
        }
        return;
      }
      const dx = e.clientX - lastX, dy = e.clientY - lastY;
      if (dx || dy) moved = true;
      if (grabbed) {
        const [mx, my] = localPoint(e);
        const vertical = grabMode === "y";
        const planeY = grabbed.y || 0.3;
        const w = screenToPlane(mx, my, planeY, vertical,
                                [grabbed.x || 0, planeY, grabbed.z || 0]);
        if (w) {
          if (vertical) {
            grabbed.y = Math.max(0, w[1] + grabOff[1]);
          } else {
            grabbed.x = w[0] + grabOff[0];
            grabbed.z = Math.max(0, w[2] + grabOff[2]);
          }
          grabbed.kind = (grabbed.y || 0) >= 2.0 ? "truss" : "floor";
          if (opts.onMoveFixture) {
            opts.onMoveFixture(grabHead, grabbed.x, grabbed.y, grabbed.z);
          }
        }
        poke();
        lastX = e.clientX;
        lastY = e.clientY;
        return;
      }
      if (dragCam) {
        panBy(dx, dy);
        lastX = e.clientX;
        lastY = e.clientY;
        poke();
        return;
      }
      camTouched = true;
      yaw += dx * 0.007;
      // drag down = camera drops below the stage = the up view the user
      // wants (and it keeps responding all the way); drag up = camera
      // rises above it for a top-down view.
      pitch = clamp(pitch + dy * 0.005, -1.45, 1.5);
      lastX = e.clientX;
      lastY = e.clientY;
      poke();
    }
    function onUp(e) {
      // a click that did not move = select the fixture under the cursor.
      // The modifiers travel with it, because "select", "add to" and "take
      // a range" are three different intents and the view cannot tell them
      // apart without being told - so a ctrl+click in the room and a
      // ctrl+click in the patch list have to mean the same thing.
      if (!moved && grabbed && opts.onPick) {
        opts.onPick(grabHead, {
          shift: !!(e && e.shiftKey),
          toggle: !!(e && (e.ctrlKey || e.metaKey)),
        });
      }
      dragging = false;
      grabbed = null;
      grabMode = null;
      dragCam = false;
      canvas.style.cursor = "";
    }
    function onWheel(e) {
      e.preventDefault();
      const [wx, wy] = localPoint(e);
      camTouched = true;
      dollyTo(clamp(1 + e.deltaY * 0.0012, 0.5, 2), wx, wy);
      poke();
    }

    /* ------------------------------------------------------ touch: pinch */
    // Two fingers: pinch to dolly, drag to pan.  A tablet is a first-class
    // control surface for a lighting desk, and wheel-only zoom left it with
    // no way to move around the room at all.
    const touches = new Map();
    let pinchDist = 0;
    canvas.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "touch") return;
      touches.set(e.pointerId, [e.clientX, e.clientY]);
      if (touches.size === 2) {
        const p = [...touches.values()];
        pinchDist = Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]);
        // a second finger cancels any fixture drag it started over
        grabbed = null;
        dragging = true;
        lastX = (p[0][0] + p[1][0]) / 2;
        lastY = (p[0][1] + p[1][1]) / 2;
      }
    }, true);
    canvas.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "touch" || !touches.has(e.pointerId)) return;
      touches.set(e.pointerId, [e.clientX, e.clientY]);
      if (touches.size === 2) {
        e.preventDefault();
        e.stopPropagation();
        const p = [...touches.values()];
        const d = Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]);
        const cx = (p[0][0] + p[1][0]) / 2, cy = (p[0][1] + p[1][1]) / 2;
        if (pinchDist > 0 && d > 0) {
          dollyTo(pinchDist / d);
        }
        panBy(cx - lastX, cy - lastY);
        pinchDist = d;
        lastX = cx; lastY = cy;
        poke();
      }
    }, true);
    const dropTouch = (e) => {
      if (e.pointerType !== "touch") return;
      touches.delete(e.pointerId);
      if (touches.size < 2) pinchDist = 0;
    };
    canvas.addEventListener("pointerup", dropTouch, true);
    canvas.addEventListener("pointercancel", dropTouch, true);

    /* ------------------------------------------- keyboard free-roam */
    // WASD/QE fly, arrow keys orbit, and 1-7 are the named views.  A desk
    // is worked from the keyboard, and a 3D view you cannot drive without a
    // mouse is not usable on the tablet that a lighting op is actually
    // holding.  Bound to the canvas so it cannot fight the rest of the page.
    const keys = Object.create(null);
    let lastFrameT = 0;
    let camTouched = false;        // has the operator moved the camera?
    function touched() { camTouched = true; }
    function onKeyDown(e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      // named views, and frame-selection - checked before the held keys so
      // a tap is never eaten as a fly key
      if (!e.shiftKey && "1234567".indexOf(k) >= 0 && k.length === 1) {
        const names = ["front", "left", "right", "back", "top", "home"];
        e.preventDefault();
        touched();
        applyView(names[Number(k) - 1]);
        return;
      }
      if (k === "f" || k === "F") {
        e.preventDefault();
        touched();
        frameSelection();
        return;
      }
      if (k === "0") { e.preventDefault(); touched(); applyView("home"); return; }
      if (k === "Shift") {
        // tracked explicitly: it is the speed boost, and "Shift" is not a
        // single character, so the lowercase path above never saw it
        keys.Shift = true;
        return;
      }
      if ("wasdqe".indexOf(k) >= 0 && k.length === 1
          || k.indexOf("Arrow") === 0) {
        keys[k] = true;
        touched();
        e.preventDefault();
        // The render loop stops itself when the view is idle - it is a
        // still frame, redrawn on demand - and a held key is only ever READ
        // from that loop.  Without this kick the key was recorded and then
        // nothing looked at it, so WASD did nothing at all, which is
        // exactly what it looked like.
        ensureLoop();
      }
    }
    function onKeyUp(e) {
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      keys[k] = false;
    }
    // Arrows orbit; WASD flies.  Both are continuous, so they are applied
    // from the render loop rather than the key event.
    function applyHeldKeys(dt) {
      let moved = false;
      const orbit = 1.6 * dt;
      if (keys.ArrowLeft) { yaw -= orbit; moved = true; }
      if (keys.ArrowRight) { yaw += orbit; moved = true; }
      if (keys.ArrowUp) { pitch = clamp(pitch + orbit, -1.45, 1.5); moved = true; }
      if (keys.ArrowDown) { pitch = clamp(pitch - orbit, -1.45, 1.5); moved = true; }
      const boost = keys.Shift ? 3 : 1;
      const f = (keys.w ? 1 : 0) - (keys.s ? 1 : 0);
      const r = (keys.d ? 1 : 0) - (keys.a ? 1 : 0);
      const u = (keys.e ? 1 : 0) - (keys.q ? 1 : 0);
      if (f || r || u) {
        flyStep(f * boost, r * boost, u * boost, dt);
        moved = true;
      }
      if (moved) poke();
      return moved;
    }
    // Tab must reach the canvas, and a focused canvas must not scroll the
    // page out from under the operator when the arrows fly the camera.
    if (canvas.tabIndex < 0) canvas.tabIndex = 0;
    // Show when the view has the keyboard.  Otherwise "WASD does nothing"
    // and "WASD is not bound here" look identical from the outside, which
    // is exactly the confusion that hid the real bug.
    const showKb = (on) => {
      canvas.classList.toggle("kbfocus", on);
      if (container && container.classList) {
        container.classList.toggle("kbfocus", on);
      }
      if (opts.onCamera) opts.onCamera(cameraState());
    };
    canvas.addEventListener("focus", () => showKb(true));
    canvas.addEventListener("blur", () => showKb(false));
    canvas.addEventListener("keydown", onKeyDown);
    canvas.addEventListener("keyup", onKeyUp);
    canvas.addEventListener("blur", () => {
      Object.keys(keys).forEach((k) => { keys[k] = false; });
    });
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointercancel", onUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    // right-drag pans, so the browser's own context menu must not eat it
    canvas.addEventListener("contextmenu", (e) => e.preventDefault());

    if (typeof ResizeObserver !== "undefined") {
      resizeObs = new ResizeObserver(() => resize());
      resizeObs.observe(container);
    } else {
      window.addEventListener("resize", resize);
    }

    /* ------------------------------------------------------ GL bring-up */
    if (gl) {
      try {
        initGL();
      } catch (e) {
        progs = false;
        posterCtx = lctx;      // draw the poster on the overlay canvas
        labelsShare = true;
        console.warn("Viz: WebGL setup failed -", e && e.message ? e.message : e);
      }
    }

    resize();
    draw();                                       // first frame synchronously -
    if (!opts.still) raf = requestAnimationFrame(tick);   // rAF may be throttled (hidden window)
    if (opts.onChange) opts.onChange(0);

    function destroy() {
      destroyed = true;
      cancelAnimationFrame(raf);
      raf = 0;
      clearTimeout(timer);
      if (resizeObs) { resizeObs.disconnect(); resizeObs = null; }
      else window.removeEventListener("resize", resize);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointercancel", onUp);
      canvas.removeEventListener("wheel", onWheel);
      // the camera's listeners too: a rebuilt view used to leave its key
      // and touch handlers bound to a canvas nobody can reach any more
      canvas.removeEventListener("keydown", onKeyDown);
      canvas.removeEventListener("keyup", onKeyUp);
      // The twin's per-definition GPU buffers die with the context, but
      // deleting them explicitly means a destroyed view leaves nothing
      // behind even if the context is kept, which is what WEBGL_lose_context
      // being unavailable looks like.
      if (twin && gl) {
        try { twin.defs.forEach(freeTwin); } catch (e) { /* ignore */ }
        twin = null;
      }
      if (gl) {
        try {
          const ext = gl.getExtension("WEBGL_lose_context");
          if (ext) ext.loseContext();
        } catch (x) { /* ignore */ }
        gl = null;
      }
      progs = false;
      if (wrapper.parentNode) wrapper.parentNode.removeChild(wrapper);
      ops.length = 0;
      beamList.length = 0;
      SB.n = 0;
      lctx = null;
      posterCtx = null;
    }

    return {
      play: play,
      pause: pause,
      step: step,
      go: go,
      destroy: destroy,
      redraw: function () { draw(); },
      toggle: function () { if (playing) pause(); else play(); },

      /* ---- live mode: the SERVER owns playback, we only interpolate ---
       *
       * The lite feed only carries heads when the patch revision changes,
       * which is right for structure and wrong for light: a 4-second cue
       * fade must animate, not step ten times a second.  So the console
       * instantiates Viz in live mode and calls setLooks() whenever the
       * per-tick look feed reports new values; the engine already
       * interpolates the fade at output rate, and this smooths what the
       * eye sees between feed ticks.
       *
       * target: {head_no: {hex, a, pan?, tilt?}}  (heads absent -> dark)
       * `pan`/`tilt` are turns (0..1) and are present only when the
       * fixture has that channel and something is driving it; absent means
       * "not driven" and the head keeps its geometric aim.
       * Returns true when something changed, so the caller can skip a
       * redraw of an unchanged frame.
       */
      setLooks: function (target, holdMs) {
        if (destroyed) return false;
        const next = target || {};
        // ease each head toward the new value, so a 20 Hz feed reads as a
        // continuous ramp rather than 20 discrete steps
        const HOLD = holdMs === undefined ? 120 : Math.max(0, holdMs);
        // carry the aim through verbatim - a key that is absent must stay
        // absent, because absent is what tells beamOf to keep its geometric
        // fallback rather than snapping a static fixture to some default
        const keep = (row) => {
          const out = { hex: row.hex, a: row.a };
          if (typeof row.pan === "number") out.pan = row.pan;
          if (typeof row.tilt === "number") out.tilt = row.tilt;
          return out;
        };
        if (!liveCur) {
          liveCur = Object.create(null);
          Object.keys(next).forEach((k) => {
            liveCur[k] = keep(next[k]);
          });
          liveFrom = JSON.parse(JSON.stringify(liveCur));
          liveT0 = 0;
          syncAim();
          draw();
          return true;
        }
        liveFrom = JSON.parse(JSON.stringify(liveCur));
        Object.keys(next).forEach((k) => {
          liveCur[k] = keep(next[k]);
        });
        // heads that vanished from the feed fade out instead of snapping
        Object.keys(liveFrom).forEach((k) => {
          if (!(k in next)) liveCur[k] = { hex: liveFrom[k].hex, a: 0 };
        });
        liveT0 = performance.now();
        liveDur = HOLD;
        syncAim();
        ensureLoop();
        return true;
      },

      /* Introspection: the beam actually computed for one head.
       *
       * beamOf is otherwise unreachable, and "the head tilts" is a claim
       * that has to be measured rather than eyeballed - a screenshot of a
       * WebGL canvas is not evidence, and this machine cannot capture one
       * anyway.  This returns the same numbers the renderer used, so the
       * geometry can be asserted on instead of assumed.
       */
      beam: function (headNo) {
        const f = (stage.fixtures || []).find(
          (x) => x.head_no === headNo);
        if (!f) return null;
        const b = beamOf(f);
        return {
          head_no: f.head_no, hang: b.hang, aimed: b.aimed,
          pan: b.pan, tilt: b.tilt,
          dir: [b.dx, b.dy, b.dz],
          from: [b.ox, b.oy, b.oz],
          to: [b.ex, b.ey, b.ez],
          len: b.len,
          look_pan: (b.look && typeof b.look.pan === "number")
                    ? b.look.pan : null,
          look_tilt: (b.look && typeof b.look.tilt === "number")
                     ? b.look.tilt : null,
        };
      },

      /* Hand over the GDTF twin's scene.
       *
       * The visualiser does not fetch, parse, own or define any of it - it
       * is handed a scene object and draws whatever geometry the definitions
       * in it carry.  That is the whole integration: everything that decides
       * WHAT a fixture looks like lives in gdtf3d.js and the GDTF, and
       * everything that decides how to put pixels on the screen lives here.
       *
       * Passing null puts the visualiser back exactly as it was, which is
       * what happens on a fixture with no geometry, on a failed fetch, and
       * on a machine with no WebGL.  There is no state in which a bad or
       * missing GDTF changes the existing rendering - that is the property
       * that makes it safe to ship at all. */
      setTwin: function (scene) {
        if (twin && twin !== scene && gl) {
          // Free the old definitions' GPU resources; keeping them is how a
          // re-import turns into a slow leak.
          twin.defs.forEach(freeTwin);
        }
        twin = scene || null;
        return !!twin;
      },
      twinProbe: twinProbe,

      /* What the 2D body pass is doing, and why.  The sprite suppression
       * keys off the head number the 2D pass carries, and if that number
       * is not the twin's, every suppression misses and the operator sees
       * a 2D box on top of a perfectly good 3D model.  `spritesSuppressed`
       * reads 0 in exactly that case and looks like good news. */
      spriteProbe: function () {
        const list = stage.fixtures || [];
        return {
          count: list.length,
          suppressed: twinSuppressed,
          drawn: twinDrawn,
          fixtures: list.map((f) => ({
            head_no: f.head_no != null ? f.head_no : null,
            n: f.n != null ? f.n : null,
            role: f.role || null,
            model: f.model || null,
            twinKnows: twin ? !!twin.get(f.head_no != null ? f.head_no : f.n)
                            : null,
            suppressed: twinHasModel(f),
          })),
        };
      },

      twinStats: function () {
        if (!twin) return null;
        const s = twin.stats();
        s.drawn = twinDrawn;
        s.nodesDrawn = twinDrew2;
        s.spritesSuppressed = twinSuppressed;
        s.instancesSkipped = twinSkipped;
        s.uploaded = 0;
        if (twin.defs) {
          twin.defs.forEach((d) => {
            if (d.gl) s.uploaded += Object.keys(d.gl).length;
          });
        }
        return s;
      },

      /* Fixture geometry changed (re-patch, drag in the rig editor). */
      setFixtures: function (fixtures) {
        if (destroyed) return;
        stage.fixtures = fixtures || [];
        rebuildRoles();
        draw();
      },

      /* Mark which heads the operator has selected.
       *
       * The selection is the single source of truth for "is this light
       * picked", and it lives on the fixture objects so the 3D view, the
       * patch list and the keyboard can never disagree about it.  Returns
       * whether anything moved, so an unchanged selection is free.
       */
      setSelected: function (heads) {
        const set = new Set((heads || []).map(Number));
        let changed = false;
        (stage.fixtures || []).forEach((f) => {
          const on = set.has(f.head_no);
          if (!!f.sel !== on) changed = true;
          f.sel = on;
        });
        if (changed) poke();
        return changed;
      },
      /* Briefly emphasise a light that was just clicked in the view, so the
       * click has a visible consequence even before the selection feed
       * comes back.  Clears itself after a moment. */
      flash: function (headNo) {
        const f = (stage.fixtures || []).find((x) => x.head_no === headNo);
        if (!f) return;
        f.picked = true;
        poke();
        clearTimeout(flashTimer);
        flashTimer = setTimeout(() => {
          f.picked = false;
          poke();
        }, 900);
      },
      selected: function () {
        return (stage.fixtures || []).filter((f) => f.sel).map((f) => f.head_no);
      },

      setPositions: function (moves) {
        if (destroyed || !moves) return;
        (stage.fixtures || []).forEach((f) => {
          const m = moves[f.head_no];
          if (!m) return;
          if (typeof m.x === "number") f.x = m.x;
          if (typeof m.y === "number") f.y = m.y;
          if (typeof m.z === "number") f.z = m.z;
          if (m.role) { f.role = m.role; addRole(m.role); }
        });
        draw();
      },
      index: function () { return cueIndex; },
      pitch: function () { return pitch; },
      yaw: function () { return yaw; },

      /* ------------------------------------------------ camera control */
      // The camera is now state the caller owns and can save, so a rebuild,
      // a patch reload or a layout change cannot throw away where the
      // operator was looking.  That was the actual complaint: the view reset
      // itself every time anything moved.
      camera: cameraState,
      setCamera: setCamera,
      // frame the selection (or the whole rig when nothing is selected)
      frame: function (heads) { frameSelection(heads); },
      // named views, matching the number keys 1-7 and 0
      view: applyView,
      zoom: function (factor) { dollyTo(factor); poke(); },
      /** True while the operator is driving the camera themselves.  The
       *  console uses this to stop auto-framing out from under them. */
      touched: function () { return camTouched; },
    };
  }

  window.Viz = { create: create, landing: landing };
})();
