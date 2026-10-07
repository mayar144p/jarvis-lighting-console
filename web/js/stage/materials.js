// Shaders for the stage: volumetric beams, and surfaces lit by those beams,
// written in three.js's node language (TSL) for its WebGPU renderer (which
// falls back to WebGL 2 by itself on a computer without WebGPU).
//
// Every beam in the rig is a real light source for the floor, the walls,
// the truss and the performers: `LIGHTS` is one set of uniforms shared by
// every surface material, refreshed once per frame by the stage.  That is
// what makes a beam LAND somewhere - a pool on the deck, a gobo on the
// cyc - instead of fading into nothing.
import * as THREE from "three";
import {
  Fn, If, Loop, Continue, uniform, uniformArray, texture, float, int, vec2, vec3, vec4,
  positionWorld, positionLocal, positionGeometry, positionView, normalWorld, cameraPosition,
  frontFacing, select, smoothstep, mix, max, min, abs, fract, floor, sin, cos, atan, sqrt, pow,
  dot, cross, normalize, length, reflect, clamp, exp, mod, varying, instanceIndex, hash,
  transformNormalToView,
} from "three/tsl";

export const MAX_LIGHTS = 32;
const SHADOW_SLOTS = 8;

// an array uniform the stage writes into in place (LIGHTS.uPos.value[i]...)
const arrayOf = (n, make, type) => {
  const value = Array.from({ length: n }, make);
  return { value, node: uniformArray(value, type) };
};
// a white pixel until the real texture arrives
const WHITE = new THREE.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1);
WHITE.needsUpdate = true;

export const LIGHTS = {
  uCount: uniform(0, "int"),
  uPos: arrayOf(MAX_LIGHTS, () => new THREE.Vector3(), "vec3"),
  uDir: arrayOf(MAX_LIGHTS, () => new THREE.Vector3(0, -1, 0), "vec3"),
  uCol: arrayOf(MAX_LIGHTS, () => new THREE.Vector3(), "vec3"),
  // x = cos(outer), y = cos(inner), z = gobo id (0 = open), w = gobo angle
  uCone: arrayOf(MAX_LIGHTS, () => new THREE.Vector4(0.9, 0.95, 0, 0), "vec4"),
  uAmbient: uniform(new THREE.Color(0x0c0c0e)),
  uTime: uniform(0),
  uBounce: uniform(1),                  // crowd dancing, 0..1
  uGobos: { value: WHITE },             // the atlas of real gobo pictures
  // shadows (shadows.js): the first uShadowCount lights each have a depth
  // picture in a cell of uShadowMap, seen through uShadowMat[i]
  uShadowMap: { value: null },
  uShadowMat: arrayOf(SHADOW_SLOTS, () => new THREE.Matrix4(), "mat4"),
  uShadowCount: uniform(0, "int"),
  // the renderer's conventions (shadows.js sets them): depth 0..1 (WebGPU)
  // or -1..1 (WebGL), and whether a texture's rows run top-down
  uZ01: uniform(0),
  uFlipY: uniform(0),
  // x = gobo blur (focus off the sharp point, frost), y = prism facets
  // (0 = none, -n = a linear prism of n), z = the prism's turn, w = half
  // of a split-colour beam (0 = all of it, 1 / 2 = one side)
  uLook: arrayOf(MAX_LIGHTS, () => new THREE.Vector4(), "vec4"),
};

// Real gobo pictures (the fixture file's own, app/fixlib gobos.zip), drawn
// into one atlas of 8 x 8 cells as they arrive.  A light's cone.z of 100 + n
// samples cell n; 1..7 are the drawn patterns, for lights whose file names
// no pictures.
const CELL = 128, PER = 8;
export class GoboAtlas {
  constructor(load) {
    this.load = load;                       // ref -> Promise<Blob>
    this.canvas = document.createElement("canvas");
    this.canvas.width = this.canvas.height = CELL * PER;
    const ctx = this.canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, CELL * PER, CELL * PER);
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.NoColorSpace;
    this.cells = new Map();                 // ref -> {cell, ready}
    this.order = [];                        // least recently used first
    this.onReady = null;
    LIGHTS.uGobos.value = this.tex;
  }

  /** The cell for a picture; -1 while it is still loading, -2 if it can't be had. */
  cell(ref) {
    let e = this.cells.get(ref);
    if (!e) {
      if (this.cells.size >= PER * PER) this._evict();
      const used = new Set([...this.cells.values()].map((x) => x.cell));
      let n = 0;
      while (used.has(n)) n++;
      e = { cell: n, ready: false };
      this.cells.set(ref, e);
      this._fill(ref, e);
    }
    const i = this.order.indexOf(ref);
    if (i >= 0) this.order.splice(i, 1);
    this.order.push(ref);
    return e.ready ? e.cell : e.failed ? -2 : -1;
  }

  _evict() {
    const ref = this.order.shift();
    if (ref) this.cells.delete(ref);
  }

  async _fill(ref, e) {
    try {
      const blob = await this.load(ref);
      const url = URL.createObjectURL(blob);
      const img = new Image();
      await new Promise((ok, bad) => { img.onload = ok; img.onerror = bad; img.src = url; });
      URL.revokeObjectURL(url);
      const tmp = document.createElement("canvas");
      tmp.width = tmp.height = CELL;
      const t = tmp.getContext("2d", { willReadFrequently: true });
      t.drawImage(img, 0, 0, CELL, CELL);
      const d = t.getImageData(0, 0, CELL, CELL);
      const px = d.data;
      // an outline drawing (metal drawn, holes clear) lets light through
      // where it is clear; a glass picture by how bright it is
      let clear = 0;
      for (let i = 3; i < px.length; i += 4) if (px[i] < 250) clear++;
      const byAlpha = clear > px.length / 4 * 0.02;
      for (let i = 0; i < px.length; i += 4) {
        const m = byAlpha ? 255 - px[i + 3] : Math.round(0.3 * px[i] + 0.59 * px[i + 1] + 0.11 * px[i + 2]);
        px[i] = px[i + 1] = px[i + 2] = m;
        px[i + 3] = 255;
      }
      if (this.cells.get(ref) !== e) return;          // evicted meanwhile
      this.canvas.getContext("2d").putImageData(d, (e.cell % PER) * CELL, Math.floor(e.cell / PER) * CELL);
      this.tex.needsUpdate = true;
      e.ready = true;
      if (this.onReady) this.onReady(ref);
    } catch (err) {
      e.failed = true;                                 // stays -1: the drawn pattern instead
    }
  }
}

// Gobo patterns, drawn analytically, or a real picture from the atlas.
// `uv` is the position inside the beam, -1..1 across its diameter.
const goboShape = Fn(([id, uv]) => {
  const r = length(uv);
  const a = atan(uv.y, uv.x);
  const out = float(1).toVar();
  If(id.greaterThan(99.5), () => {                       // a real gobo picture
    const n = id.sub(100);
    const cell = vec2(mod(n, PER), floor(n.div(PER)));
    const p = clamp(uv.mul(0.5).add(0.5), 0.004, 0.996);
    const at = cell.add(vec2(p.x, p.y.oneMinus())).div(PER);
    out.assign(texture(LIGHTS.uGobos.value, vec2(at.x, at.y.oneMinus())).r);
  }).ElseIf(id.lessThan(0.5), () => {
    out.assign(1);
  }).ElseIf(id.lessThan(1.5), () => {                    // dot ring
    const k = float(0).toVar();
    Loop(8, ({ i }) => {
      const t = float(i).mul(0.785398);
      k.assign(max(k, smoothstep(0.13, 0.17, length(uv.sub(vec2(cos(t), sin(t)).mul(0.58)))).oneMinus()));
    });
    out.assign(max(k, smoothstep(0.16, 0.2, r).oneMinus()));
  }).ElseIf(id.lessThan(2.5), () => {                    // star
    const st = pow(abs(cos(a.mul(2.5))), 6).mul(0.45).add(0.35);
    out.assign(smoothstep(st.sub(0.04), st.add(0.04), r).oneMinus());
  }).ElseIf(id.lessThan(3.5), () => {                    // bars
    out.assign(smoothstep(0.35, 0.45, abs(fract(uv.x.mul(2.5)).sub(0.5)).mul(2)));
  }).ElseIf(id.lessThan(4.5), () => {                    // rings
    out.assign(smoothstep(0.3, 0.5, abs(fract(r.mul(3)).sub(0.5)).mul(2)));
  }).ElseIf(id.lessThan(5.5), () => {                    // petals
    out.assign(smoothstep(0.2, 0.35, abs(sin(a.mul(3))).mul(r.mul(0.6).oneMinus())));
  }).ElseIf(id.lessThan(6.5), () => {                    // breakup
    const p = uv.mul(3.2);
    const nn = sin(p.x.mul(1.7).add(sin(p.y.mul(2.3)))).mul(cos(p.y.mul(1.3).add(sin(p.x.mul(1.9)))));
    out.assign(smoothstep(-0.05, 0.25, nn));
  }).Else(() => {                                        // cross
    out.assign(smoothstep(0.08, 0.12, min(abs(uv.x), abs(uv.y))).oneMinus());
  });
  return out;
});

// look: x = blur (focus off the sharp point, frost), y = prism facets (-n
// linear), z = the prism's turn, w = which half of a split beam.  Out of
// focus the picture's contrast melts into the pool (one look at the
// picture, not several: this runs for every pixel of the room).  A prism
// splits the beam into copies round its centre (a linear one: in a row).
// Low quality (detail off): the picture as it is, and no halves.
const goboMask = (id, uv0, look, detail) => {
  if (!detail) return goboShape(id, uv0);
  return Fn(() => {
    const uv = vec2(uv0).toVar();
    const edge = float(1).toVar();
    const n = abs(look.y);
    If(n.greaterThan(1.5), () => {
      const cs = cos(look.z), sn = sin(look.z);
      const q = vec2(cs.mul(uv.x).add(sn.mul(uv.y)), sn.negate().mul(uv.x).add(cs.mul(uv.y)));
      If(look.y.lessThan(0), () => {
        // a linear prism: n copies side by side
        const sp = float(1.6).div(n);
        const k = clamp(floor(q.x.div(sp).add(n.mul(0.5))), 0, n.sub(1));
        uv.assign(vec2(q.x.sub(k.sub(n.sub(1).mul(0.5)).mul(sp)), q.y).div(sp.mul(0.62)));
      }).Else(() => {
        // the copy this point belongs to (round the centre; they hardly overlap)
        const step = float(6.2831853).div(n);
        const t = floor(atan(q.y, q.x).div(step).add(0.5)).mul(step);
        const rr = min(0.53, sin(float(3.14159265).div(n)).mul(0.58));
        uv.assign(q.sub(vec2(cos(t), sin(t)).mul(0.47)).div(rr));
      });
      edge.assign(smoothstep(look.x.mul(0.3).oneMinus().sub(0.1), 1.0, length(uv)).oneMinus());
    });
    If(look.w.greaterThan(0.5), () => {
      // one half of a beam split between two colour filters
      const s = select(look.w.greaterThan(1.5), uv0.x.negate(), uv0.x);
      edge.mulAssign(smoothstep(-0.04, 0.04, s));
    });
    const out = edge.toVar();
    If(id.greaterThan(0.5), () => {
      out.assign(mix(goboShape(id, uv), 0.55, min(0.85, look.x)).mul(edge));
    });
    return out;
  })();
};

// how much of light i reaches this point past the crowd, the objects and
// the stage (1 = all of it); 2 x 2 taps for a soft edge.  The atlas: 4
// across, 2 down, 512 x 512 each.
const shadowAt = Fn(([i, wp]) => {
  const k = float(1).toVar();
  const sp = LIGHTS.uShadowMat.node.element(i).mul(vec4(wp, 1));
  If(sp.w.greaterThan(0), () => {
    const nd = sp.xyz.div(sp.w);
    If(abs(nd.x).lessThan(1).and(abs(nd.y).lessThan(1)).and(nd.z.lessThan(1)), () => {
      const v = nd.y.mul(0.5).add(0.5);
      const uv = vec2(nd.x.mul(0.5).add(0.5), select(LIGHTS.uFlipY.greaterThan(0.5), v.oneMinus(), v));
      const fi = float(i);
      const off = vec2(mod(fi, 4).mul(0.25), floor(fi.div(4)).mul(0.5));
      const d = select(LIGHTS.uZ01.greaterThan(0.5), nd.z, nd.z.mul(0.5).add(0.5)).sub(0.0004);
      const px = 1.5 / 512;
      const sum = float(0).toVar();
      for (const [ox, oy] of [[-0.5, -0.5], [0.5, -0.5], [-0.5, 0.5], [0.5, 0.5]]) {
        const q = clamp(uv.add(vec2(ox * px, oy * px)), 0.002, 0.998);
        const z = texture(LIGHTS.uShadowMap.value, off.add(q.mul(vec2(0.25, 0.5)))).x;
        sum.addAssign(select(d.lessThanEqual(z), float(1), float(0)));
      }
      k.assign(sum.mul(0.25));
    });
  });
  return k;
});

/** The light every beam puts on a surface point (and, for a shiny one,
 *  the lenses it mirrors). */
const surfaceLight = (albedo, grid, sheen, detail) => Fn(() => {
  const wp = positionWorld;
  const N = select(frontFacing, normalize(normalWorld), normalize(normalWorld).negate()).toVar();
  const light = vec3(LIGHTS.uAmbient).mul(max(N.y, 0).mul(0.4).add(0.6)).toVar();
  const gloss = vec3(0).toVar();
  // where this point would show a light reflected (once, not per light)
  const R = detail ? reflect(normalize(wp.sub(cameraPosition)), N) : null;
  Loop({ start: int(0), end: LIGHTS.uCount, type: "int", condition: "<" }, ({ i }) => {
    const L = wp.sub(LIGHTS.uPos.node.element(i)).toVar();
    const dir = LIGHTS.uDir.node.element(i);
    const along = dot(L, dir);
    If(along.lessThanEqual(0), () => { Continue(); });          // behind the lens
    const d2 = dot(L, L);
    const cone = LIGHTS.uCone.node.element(i);
    If(along.mul(along).lessThanEqual(cone.x.mul(cone.x).mul(d2)), () => { Continue(); });  // outside the cone
    const d = sqrt(d2);
    const Ld = L.div(max(d, 1e-4));
    const spot = smoothstep(cone.x, cone.y, along.div(max(d, 1e-4))).toVar();
    const look = LIGHTS.uLook.node.element(i);
    If(cone.z.greaterThan(0.5).or(abs(look.y).greaterThan(0.5)).or(look.w.greaterThan(0.5)), () => {
      const up = select(abs(dir.y).lessThan(0.99), vec3(0, 1, 0), vec3(1, 0, 0));
      const rt = normalize(cross(dir, up));
      const u2 = cross(rt, dir);
      const tanO = sqrt(max(cone.x.mul(cone.x).oneMinus(), 1e-5)).div(cone.x);
      const uv0 = vec2(dot(L, rt), dot(L, u2)).div(max(along.mul(tanO), 1e-4));
      const cs = cos(cone.w), sn = sin(cone.w);
      const uv = vec2(cs.mul(uv0.x).add(sn.mul(uv0.y)), sn.negate().mul(uv0.x).add(cs.mul(uv0.y)));
      spot.mulAssign(goboMask(cone.z, uv, look, detail));
    });
    const lam = max(dot(N, Ld.negate()), 0);
    const att = float(1).div(d2.mul(0.09).add(1));
    If(i.lessThan(LIGHTS.uShadowCount).and(spot.mul(lam).greaterThan(0.001)), () => {
      spot.mulAssign(shadowAt(i, wp));
    });
    const col = LIGHTS.uCol.node.element(i);
    light.addAssign(col.mul(spot).mul(lam).mul(att));
    if (detail) {
      const rs = max(dot(R, Ld.negate()), 0);
      gloss.addAssign(col.mul(spot).mul(pow(rs, 900).mul(6).add(pow(rs, 60).mul(0.12))));
    }
  });
  const alb = vec3(albedo).toVar();
  If(grid.greaterThan(0), () => {                     // a faint metre grid on the deck
    const g = abs(fract(wp.xz).sub(0.5));
    const line = smoothstep(0.0, 0.012, max(g.x, g.y).oneMinus().sub(0.5)).oneMinus();
    alb.mulAssign(line.mul(grid).add(1));
  });
  // a hint of a gloss floor: the brightest pools bloom a little
  return alb.mul(light).add(light.mul(sheen).mul(0.04)).add(gloss.mul(sheen));
})();

// Medium and High draw the detail (focus and frost on gobos, prisms, lenses
// mirrored in shiny floors); Low leaves it out - it costs every pixel of
// the room on every light, and Low is for computers that can't spare it.
export const DETAIL = { on: true };
export function setDetail(scene, on) {
  DETAIL.on = !!on;
  scene.traverse((o) => {
    for (const m of [].concat(o.material || [])) {
      if (!m || !m.userData.detail || m.userData.detailOn === DETAIL.on) continue;
      m.userData.detail(DETAIL.on);
      m.needsUpdate = true;
    }
  });
}

/** A diffuse surface that every beam in the rig lights. */
export function surfaceMaterial(albedo, opts = {}) {
  const m = new THREE.MeshBasicNodeMaterial({ side: opts.side || THREE.FrontSide });
  // the same handles the ShaderMaterial had: m.uniforms.uAlbedo.value...
  m.uniforms = {
    uAlbedo: uniform(new THREE.Color(albedo)),
    uGrid: uniform(opts.grid || 0),
    uSheen: uniform(opts.sheen || 0),
  };
  m.userData.detail = (on) => {
    m.userData.detailOn = on;
    m.colorNode = surfaceLight(m.uniforms.uAlbedo, m.uniforms.uGrid, m.uniforms.uSheen, on);
  };
  m.userData.detail(DETAIL.on);
  return m;
}

// People: the same beam-lit surface, instanced, one colour per person (the
// instance colour), with a small per-person bounce so a dance floor looks
// like one.  Lit per vertex: they are small on screen, and a dense dance
// floor lit per pixel was the most expensive thing in the room.
const crowdLight = Fn(() => {
  const wp = positionWorld;
  const N = normalize(normalWorld);
  const light = vec3(LIGHTS.uAmbient).mul(max(N.y, 0).mul(0.4).add(0.6)).toVar();
  Loop({ start: int(0), end: LIGHTS.uCount, type: "int", condition: "<" }, ({ i }) => {
    const L = wp.sub(LIGHTS.uPos.node.element(i));
    const along = dot(L, LIGHTS.uDir.node.element(i));
    If(along.lessThanEqual(0), () => { Continue(); });
    const d2 = dot(L, L);
    const cone = LIGHTS.uCone.node.element(i);
    If(along.mul(along).lessThanEqual(cone.x.mul(cone.x).mul(d2)), () => { Continue(); });
    const d = sqrt(d2);
    const spot = smoothstep(cone.x, cone.y, along.div(d));
    const lam = max(dot(N, L.div(d).negate()), 0).mul(0.65).add(0.35);
    light.addAssign(LIGHTS.uCol.node.element(i).mul(spot).mul(lam).div(d2.mul(0.09).add(1)));
  });
  return light;
});

const crowdCache = new Map();

export function crowdMaterial(albedo) {
  const key = String(albedo);
  if (crowdCache.has(key)) return crowdCache.get(key);
  const m = new THREE.MeshBasicNodeMaterial();
  m.uniforms = { uAlbedo: uniform(new THREE.Color(albedo)) };
  const ph = hash(instanceIndex);
  const hop = max(0, sin(LIGHTS.uTime.mul(ph.mul(0.4).add(1.9)).add(ph).mul(6.2831))).mul(LIGHTS.uBounce).mul(0.05);
  m.positionNode = positionLocal.add(vec3(0, hop, 0));
  m.colorNode = vec3(m.uniforms.uAlbedo).mul(varying(crowdLight()));   // x the instance colour
  crowdCache.set(key, m);
  return m;
}

// ---------------------------------------------------------------------------
// Beams: an open cone, scaled in the vertex stage so one geometry serves
// every beam.  Additive, depth-tested (a fixture body hides the beam behind
// it), never depth-written (beams do not hide each other).
// ---------------------------------------------------------------------------
let coneGeometry = null;

/** The shared unit cone: unit circle in x/z, y from 0 (lens) to 1 (end). */
export function beamGeometry() {
  if (coneGeometry) return coneGeometry;
  const g = new THREE.CylinderGeometry(1, 1, 1, 40, 12, true);
  g.translate(0, 0.5, 0);
  coneGeometry = g;
  return g;
}

// the haze's drift: cheap value noise (the old beams' own), not Perlin -
// it runs for every pixel of every beam, twice
const hash3 = Fn(([p0]) => {
  const p = fract(p0.mul(0.3183099).add(0.1)).mul(17);
  return fract(p.x.mul(p.y).mul(p.z).mul(p.x.add(p.y).add(p.z)));
});
const valueNoise = Fn(([x]) => {
  const i = floor(x);
  const f0 = fract(x);
  const f = f0.mul(f0).mul(f0.mul(-2).add(3));
  const h = (dx, dy, dz) => hash3(i.add(vec3(dx, dy, dz)));
  return mix(mix(mix(h(0, 0, 0), h(1, 0, 0), f.x), mix(h(0, 1, 0), h(1, 1, 0), f.x), f.y),
    mix(mix(h(0, 0, 1), h(1, 0, 1), f.x), mix(h(0, 1, 1), h(1, 1, 1), f.x), f.y), f.z);
});

const beamNodes = (u, detail) => {
  const t = positionGeometry.y;
  const r = mix(u.uR0, u.uR1, t);
  const position = vec3(positionGeometry.x.mul(r), t.mul(u.uLen), positionGeometry.z.mul(r));
  const vT = varying(t);
  const vRad = varying(positionGeometry.xz);
  const vViewNormal = varying(transformNormalToView(vec3(positionGeometry.x, 0, positionGeometry.z)));
  const color = Fn(() => {
    const facing = abs(dot(normalize(vViewNormal), normalize(positionView.negate())));
    const edge = pow(facing, u.uSoft);
    const dist = vT.mul(u.uLen);
    const along = exp(dist.mul(-0.085)).mul(smoothstep(0, 0.03, vT)).mul(smoothstep(0.93, 1, vT).mul(0.6).oneMinus());
    const q = positionWorld.mul(0.7).add(vec3(LIGHTS.uTime.mul(0.07), LIGHTS.uTime.mul(0.03), LIGHTS.uTime.mul(0.05)));
    const n = valueNoise(q).mul(0.65).add(valueNoise(q.mul(2.3)).mul(0.35));
    const haze = n.mul(0.45).add(0.55);
    const a = u.uIntensity.mul(edge).mul(along).mul(mix(1, haze, 0.8)).mul(u.uHaze).toVar();
    If(u.uGobo.greaterThan(0.5).or(abs(u.uLook.y).greaterThan(0.5)), () => {
      // a gobo in haze: shafts.  The line of sight through this point of
      // the cone passes the beam's axis at about sqrt(1 - facing^2) of its
      // radius; the picture there lights it or leaves it dark
      const uv0 = normalize(vRad).mul(sqrt(max(0, facing.mul(facing).oneMinus())));
      const cs = cos(u.uGoboRot), sn = sin(u.uGoboRot);
      const uv = vec2(cs.mul(uv0.x).add(sn.mul(uv0.y)), sn.negate().mul(uv0.x).add(cs.mul(uv0.y)));
      a.mulAssign(goboMask(u.uGobo, uv, vec4(u.uLook.xyz, 0), detail).mul(0.95).add(0.2));
    });
    return a;
  });
  // a split beam: one side of the cone one colour, the other the second
  const tint = mix(vec3(u.uColor), vec3(u.uColor2), smoothstep(-0.05, 0.05, vRad.x.negate()).mul(u.uLook.w));
  return { position, alpha: color(), tint };
};

export function beamMaterial() {
  const m = new THREE.MeshBasicNodeMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    forceSinglePass: true,              // additive light needs no back-then-front order
  });
  // the same handles the ShaderMaterial had (stage.js sets .value each frame)
  m.uniforms = {
    uColor: uniform(new THREE.Color(1, 1, 1)),
    uColor2: uniform(new THREE.Color(1, 1, 1)),     // the other half of a split beam
    uIntensity: uniform(0),
    uSoft: uniform(1.6),
    uHaze: uniform(0.6),
    uLen: uniform(5),
    uR0: uniform(0.05),
    uR1: uniform(1),
    uGobo: uniform(0),
    uGoboRot: uniform(0),
    uLook: uniform(new THREE.Vector4()),
  };
  m.userData.detail = (on) => {
    m.userData.detailOn = on;
    const { position, alpha, tint } = beamNodes(m.uniforms, on);
    m.positionNode = position;
    m.colorNode = tint.mul(alpha);
    m.opacityNode = alpha;
  };
  m.userData.detail(DETAIL.on);
  return m;
}

// A soft radial glow for lenses seen head-on.
let glowTexture = null;
export function glowMap() {
  if (glowTexture) return glowTexture;
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.18, "rgba(255,255,255,0.55)");
  grad.addColorStop(0.5, "rgba(255,255,255,0.12)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  glowTexture = new THREE.CanvasTexture(c);
  glowTexture.colorSpace = THREE.SRGBColorSpace;
  return glowTexture;
}
