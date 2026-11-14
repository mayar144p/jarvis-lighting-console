// Beams as light in the air (Medium and High quality).
//
// What you see of a beam in haze is light the haze scatters towards you,
// all along your line of sight through the beam.  So for every pixel a
// beam covers, its shader finds where the line of sight enters and leaves
// the beam's cone, cuts that off where it meets anything solid (the depth
// picture of the room just drawn: a person, the truss, the DJ booth, a wall
// at any angle), and adds up the light scattered along what is left:
//
//   * the beam's light thins out as it spreads - the same light over a
//     wider circle - so it is brightest at the lens and fades with distance;
//   * haze sends more light on forwards than sideways, so a beam pointing
//     at you is brighter than the same beam seen from the side;
//   * at every point the gobo and the prism decide whether that part of the
//     beam is lit: shafts in the air that match the pattern on the floor;
//   * the haze drifts (a slowly moving 3D pattern);
//   * a beam with a shadow picture is dark in the air behind the truss or a
//     person standing in it.
//
// The beams are drawn at half the screen's size (light in haze is soft)
// into their own picture, and stretched back over the room with its depth
// as a guide, so a beam stops at a person's outline, not a block beside it.
// The cone mesh only marks the pixels to work on; the cone itself is maths.
import * as THREE from "three";
import {
  Fn, If, Loop, uniform, uniformArray, texture, texture3D, float, int, vec2, vec3, vec4, exp,
  positionGeometry, positionWorld, cameraPosition, cameraWorldMatrix,
  screenUV, screenCoordinate, interleavedGradientNoise, select, smoothstep, mix, max, min, abs,
  sqrt, pow, dot, normalize, length, clamp, floor, fract, cos, sin,
} from "three/tsl";
import { LIGHTS, goboMask, shadowTap, projSample, inRect } from "./materials.js";

// ------------------------------------------------------------------ shared
// A plain depth texture until the room's first picture exists.
const NO_DEPTH = new THREE.DepthTexture(1, 1);

// The haze's drift: a small repeating 3D pattern (two layers of smooth
// noise), looked up once per step instead of worked out.
function hazeTexture() {
  const N = 32, data = new Uint8Array(N * N * N);
  const lattice = (n, seed) => {
    const g = new Float32Array(n * n * n);
    let s = seed >>> 0;
    for (let i = 0; i < g.length; i++) { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; g[i] = (s >>> 0) / 4294967296; }
    return g;
  };
  const layer = (n, seed) => {
    const g = lattice(n, seed);
    const at = (x, y, z) => g[((z % n) * n + (y % n)) * n + (x % n)];
    const sm = (t) => t * t * (3 - 2 * t);
    return (x, y, z) => {
      const fx = x * n / N, fy = y * n / N, fz = z * n / N;
      const ix = Math.floor(fx), iy = Math.floor(fy), iz = Math.floor(fz);
      const tx = sm(fx - ix), ty = sm(fy - iy), tz = sm(fz - iz);
      const l = (a, b, t) => a + (b - a) * t;
      return l(l(l(at(ix, iy, iz), at(ix + 1, iy, iz), tx), l(at(ix, iy + 1, iz), at(ix + 1, iy + 1, iz), tx), ty),
        l(l(at(ix, iy, iz + 1), at(ix + 1, iy, iz + 1), tx), l(at(ix, iy + 1, iz + 1), at(ix + 1, iy + 1, iz + 1), tx), ty), tz);
    };
  };
  const a = layer(8, 1234567), b = layer(16, 7654321);
  for (let z = 0; z < N; z++) for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    data[(z * N + y) * N + x] = Math.round(255 * (a(x, y, z) * 0.65 + b(x, y, z) * 0.35));
  }
  const tex = new THREE.Data3DTexture(data, N, N, N);
  tex.format = THREE.RedFormat;
  tex.type = THREE.UnsignedByteType;
  tex.minFilter = tex.magFilter = THREE.LinearFilter;
  tex.wrapS = tex.wrapT = tex.wrapR = THREE.RepeatWrapping;
  tex.unpackAlignment = 1;
  tex.needsUpdate = true;
  return tex;
}

// an array uniform the stage writes into in place (VOL.areaC.value[i]...)
const arrayOf = (n, make, type) => {
  const value = Array.from({ length: n }, make);
  return { value, node: uniformArray(value, type) };
};
export const MAX_AREAS = 4, MAX_CLOUDS = 4;

export const VOL = {
  depth: texture(NO_DEPTH),          // the room's depth picture, this frame
  z01: uniform(1),                   // its depth runs 0..1 (WebGPU) or -1..1 (WebGL 2)
  near: uniform(0.05),               // the camera's near and far (the stage sets them)
  far: uniform(400),
  steps: uniform(16, "int"),         // most steps along a line of sight (High 24, Medium 12)
  haze: texture3D(hazeTexture()),
  g: uniform(0.35),                  // how much the haze favours forwards (0 = evenly)
  gain: uniform(0.25),               // the overall brightness of beams in haze
  // where the haze is thicker: the venue's haze areas (centre + how much
  // thicker, half its size + its turn) and the clouds round running hazers
  // (centre + radius, strength) - atmos.js
  areaCount: uniform(0, "int"),
  areaC: arrayOf(MAX_AREAS, () => new THREE.Vector4(), "vec4"),
  areaH: arrayOf(MAX_AREAS, () => new THREE.Vector4(1, 1, 1, 0), "vec4"),
  cloudCount: uniform(0, "int"),
  cloud: arrayOf(MAX_CLOUDS, () => new THREE.Vector4(0, 0, 0, 1), "vec4"),
  cloudStr: arrayOf(MAX_CLOUDS, () => 0, "float"),
};

// how far along a line of sight (from the camera, direction D) the room's
// first solid surface is at this pixel
/** How far in front of the camera (positive metres) a depth reading is. */
export const depthToDistance = (d) => {
  const n = VOL.near, f = VOL.far;
  const zGpu = n.mul(f).div(f.sub(n).mul(d).sub(f));
  const zGl = n.mul(f).mul(2).div(f.sub(n).mul(d.mul(2).sub(1)).sub(f.add(n)));
  return select(VOL.z01.greaterThan(0.5), zGpu, zGl).negate();
};

const roomDistance = Fn(([D]) => {
  const ahead = depthToDistance(VOL.depth.sample(screenUV).x);
  const fwd = cameraWorldMatrix.element(2).xyz.negate();
  return ahead.div(max(dot(D, fwd), 1e-4));
});

/** How much thicker the haze is at P than the room's: inside a haze area
 *  (soft at its edges), in a hazer's cloud. */
export const localHaze = Fn(([P]) => {
  const d = float(0).toVar();
  Loop({ start: int(0), end: VOL.areaCount, type: "int", condition: "<" }, ({ i }) => {
    const c = VOL.areaC.node.element(i), hb = VOL.areaH.node.element(i);
    const rel = P.sub(c.xyz);
    const cs = cos(hb.w), sn = sin(hb.w);
    const loc = vec3(cs.mul(rel.x).sub(sn.mul(rel.z)), rel.y, sn.mul(rel.x).add(cs.mul(rel.z)));
    const outside = max(abs(loc).sub(hb.xyz), vec3(0));
    d.addAssign(c.w.mul(smoothstep(0, 0.8, length(outside)).oneMinus()).mul(1.2));
  });
  Loop({ start: int(0), end: VOL.cloudCount, type: "int", condition: "<" }, ({ i }) => {
    const c = VOL.cloud.node.element(i);
    const q = P.sub(c.xyz);
    d.addAssign(VOL.cloudStr.node.element(i).mul(exp(dot(q, q).div(c.w.mul(c.w)).negate())).mul(1.5));
  });
  return d;
});

// Henyey-Greenstein: how much light the haze sends at angle (cos c) off its way
const phase = (c, g) => {
  const g2 = g.mul(g);
  return g2.oneMinus().div(pow(g2.add(1).sub(g.mul(c).mul(2)), 1.5));
};

// ------------------------------------------------------------- the mesh
let volGeometry = null;

/** The unit cylinder with both ends closed (y 0 = the lens .. 1 = the end):
 *  its far side marks every pixel a line of sight through the beam
 *  crosses, from outside the beam or from inside it. */
export function volumeGeometry() {
  if (volGeometry) return volGeometry;
  const g = new THREE.CylinderGeometry(1, 1, 1, 32, 1, false);
  g.translate(0, 0.5, 0);
  volGeometry = g;
  return g;
}

// ------------------------------------------------------------ the shader
const light = (u, detail) => Fn(() => {
  const C = cameraPosition;
  const D = normalize(positionWorld.sub(C)).toVar();
  const V = u.uAxis, O = u.uOrigin;
  const k = max(u.uTan, 2e-4).toVar();                 // the cone's spread: radius per metre
  const h0 = u.uR0.div(k).toVar();                     // lens to the cone's point (behind it)
  const A = O.sub(V.mul(h0)).toVar();
  const w = C.sub(A).toVar();
  const dv = dot(D, V).toVar(), wv = dot(w, V).toVar();
  const c2 = float(1).div(k.mul(k).add(1)).toVar();    // cos^2 of the half angle
  // the line against the (double) cone: a t^2 + b t + c >= 0 inside it
  const a0 = dv.mul(dv).sub(c2);
  const a = select(abs(a0).lessThan(1e-6), float(-1e-6), a0).toVar();
  const b = dv.mul(wv).sub(c2.mul(dot(D, w))).mul(2).toVar();
  const c = wv.mul(wv).sub(c2.mul(dot(w, w))).toVar();
  const disc = b.mul(b).sub(a.mul(c).mul(4)).toVar();
  const sq = sqrt(max(disc, 0)).toVar();
  const r1 = b.negate().sub(sq).div(a.mul(2)), r2 = b.negate().add(sq).div(a.mul(2));
  const lo = min(r1, r2).toVar(), hi = max(r1, r2).toVar();
  const BIG = float(1e6);
  const coneLo = float(0).toVar(), coneHi = float(0).toVar();
  If(a.lessThan(0), () => {
    // shallower than the cone's side: in between the two crossings (or never)
    coneLo.assign(select(disc.greaterThanEqual(0), lo, BIG));
    coneHi.assign(select(disc.greaterThanEqual(0), hi, BIG.negate()));
  }).Else(() => {
    // steeper: from the crossing on the lens's far side on (the other is behind the point)
    coneLo.assign(select(disc.lessThan(0), BIG.negate(), select(dv.greaterThan(0), hi, BIG.negate())));
    coneHi.assign(select(disc.lessThan(0), BIG, select(dv.greaterThan(0), BIG, lo)));
  });
  // between the lens and the beam's far end
  const dvs = select(abs(dv).lessThan(1e-6), float(1e-6), dv);
  const s0 = h0.sub(wv).div(dvs), s1 = h0.add(u.uLen).sub(wv).div(dvs);
  const tA = max(max(coneLo, min(s0, s1)), 0).toVar();
  const tB = min(min(coneHi, max(s0, s1)), roomDistance(D)).toVar();
  const out = vec3(0).toVar();
  If(tB.greaterThan(tA.add(1e-4)), () => {
    // fewer steps for a short way through a narrow beam, more for a long way
    const midR = u.uR0.add(k.mul(dot(C.add(D.mul(tA.add(tB).mul(0.5))).sub(O), V)));
    const want = tB.sub(tA).div(max(midR, 0.02).mul(0.6));
    const n = int(clamp(want, 4, float(VOL.steps))).toVar();
    const dt = tB.sub(tA).div(float(n)).toVar();
    const jitter = interleavedGradientNoise(screenCoordinate.xy);
    const sum = vec3(0).toVar();
    const drift = vec3(LIGHTS.uTime.mul(0.021), LIGHTS.uTime.mul(0.009), LIGHTS.uTime.mul(0.015));
    const gobo = u.uGobo.greaterThan(0.5).or(abs(u.uLook.y).greaterThan(0.5)).or(u.uLook.w.greaterThan(0.5));
    const side = u.uLook.w.greaterThan(0.5);
    Loop({ start: int(0), end: n, type: "int", condition: "<" }, ({ i }) => {
      const t = tA.add(float(i).add(jitter).mul(dt));
      const P = C.add(D.mul(t)).toVar();
      const rel = P.sub(O).toVar();
      const s = max(dot(rel, V), 0).toVar();
      const q = rel.sub(V.mul(s)).toVar();
      const R = u.uR0.add(s.mul(k)).toVar();
      const uv0 = vec2(dot(q, u.uRight), dot(q, u.uUp)).div(max(R, 1e-4)).toVar();
      // the beam's edge, soft or crisp (frost, a wash's softer field)
      const edge = smoothstep(u.uSoft, 1.0, length(uv0)).oneMinus().toVar();
      // the same light over a wider circle (never infinite at the lens)
      const spread = float(1).div(R.mul(R).add(0.0025));
      const toward = phase(dot(normalize(P.sub(A)), D.negate()), VOL.g);
      const drift3 = texture3D(VOL.haze.value, P.mul(0.11).add(drift)).x.mul(0.9).add(0.35);
      // the haze here: the room's, and thicker in a haze area or a hazer's cloud
      const haze = u.uHaze.add(localHaze(P)).mul(drift3);
      // a beam that ends in the air (outdoors, into the sky) fades out
      const fade = select(u.uFade.greaterThan(0.5), smoothstep(u.uLen.mul(0.7), u.uLen, s).oneMinus(), float(1));
      const lit = edge.mul(spread).mul(toward).mul(haze).mul(fade).toVar();
      If(gobo, () => {
        const cs = cos(u.uGoboRot), sn = sin(u.uGoboRot);
        const uv = vec2(cs.mul(uv0.x).add(sn.mul(uv0.y)), sn.negate().mul(uv0.x).add(cs.mul(uv0.y)));
        lit.mulAssign(goboMask(u.uGobo, uv, vec4(u.uLook.xyz, 0), detail));
      });
      If(u.uShadow.greaterThanEqual(0), () => {
        lit.mulAssign(shadowTap(u.uShadow, P));
      });
      If(u.uProj.greaterThan(-0.5), () => {
        // a projector in haze: a pyramid of its picture's own colours
        const r = vec2(uv0.x.div(u.uRect.x), uv0.y.div(u.uRect.y));
        sum.addAssign(projSample(u.uProj, r.mul(0.5).add(0.5)).mul(inRect(uv0, u.uRect.x, u.uRect.y)).mul(lit));
      }).Else(() => {
        // half one colour, half the other (a split colour wheel)
        const tint = mix(vec3(u.uColor), vec3(u.uColor2), select(side.and(uv0.x.lessThan(0)), float(1), float(0)));
        sum.addAssign(tint.mul(lit));
      });
    });
    out.assign(sum.mul(dt).mul(u.uPower).mul(VOL.gain));
  });
  return out;
})();

/** The material for one beam in haze; stage.js sets `uniforms` each frame. */
export function volumeBeamMaterial() {
  const m = new THREE.MeshBasicNodeMaterial({
    transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending,
    side: THREE.BackSide,
  });
  m.uniforms = {
    uOrigin: uniform(new THREE.Vector3()), uAxis: uniform(new THREE.Vector3(0, -1, 0)),
    uRight: uniform(new THREE.Vector3(1, 0, 0)), uUp: uniform(new THREE.Vector3(0, 0, 1)),
    uR0: uniform(0.05), uTan: uniform(0.2), uLen: uniform(5),
    uColor: uniform(new THREE.Color(1, 1, 1)), uColor2: uniform(new THREE.Color(1, 1, 1)),
    uPower: uniform(0), uSoft: uniform(0.7), uHaze: uniform(0.6), uFade: uniform(0),
    uGobo: uniform(0), uGoboRot: uniform(0), uLook: uniform(new THREE.Vector4()),
    uShadow: uniform(-1, "int"),
    // a projector's beam (projectors.js): which picture (-1 none) and its rectangle
    uProj: uniform(-1), uRect: uniform(new THREE.Vector2(1, 1)),
  };
  const u = m.uniforms;
  // the cylinder becomes the cone (a touch wider: the mesh's flat sides
  // must not cut inside the round cone)
  const t = positionGeometry.y;
  const r = u.uR0.add(t.mul(u.uLen).mul(u.uTan)).mul(1.04).add(0.01);
  m.positionNode = vec3(positionGeometry.x.mul(r), t.mul(u.uLen), positionGeometry.z.mul(r));
  m.userData.detail = (on) => {
    m.userData.detailOn = on;
    m.colorNode = vec4(light(u, on), 1);
  };
  m.userData.detail(true);
  return m;
}

/** The beams' half-size picture stretched over the full-size one: each
 *  pixel takes the nearby beam pixels that saw the same depth as it did, so
 *  a beam stops at an outline instead of bleeding over it. */
export const beamUpsample = (beamTex, size) => Fn(() => {
  const p = screenUV.mul(size).sub(0.5);
  const base = floor(p);
  const f = fract(p);
  const lin = depthToDistance;
  const here = lin(VOL.depth.sample(screenUV).x).toVar();
  const sum = vec3(0).toVar();
  const wsum = float(0).toVar();
  for (const [ox, oy] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
    const uvc = base.add(vec2(ox, oy)).add(0.5).div(size);
    const wb = (ox ? f.x : f.x.oneMinus()).mul(oy ? f.y : f.y.oneMinus());
    const z = lin(VOL.depth.sample(uvc).x);
    const wd = float(1).div(abs(z.sub(here)).div(max(here, 0.1)).mul(40).add(0.05));
    const wgt = wb.add(0.002).mul(wd);
    sum.addAssign(texture(beamTex, uvc).rgb.mul(wgt));
    wsum.addAssign(wgt);
  }
  return sum.div(max(wsum, 1e-5));
})();
