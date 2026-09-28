// Shaders for the stage: volumetric beams, and surfaces lit by those beams.
//
// Every beam in the rig is a real light source for the floor, the walls,
// the truss and the performers: `LIGHTS` is one uniform block shared by
// every surface material, refreshed once per frame by the stage.  That is
// what makes a beam LAND somewhere - a pool on the deck, a gobo on the
// cyc - instead of fading into nothing.
import * as THREE from "three";

export const MAX_LIGHTS = 32;

export const LIGHTS = {
  uCount: { value: 0 },
  uPos: { value: Array.from({ length: MAX_LIGHTS }, () => new THREE.Vector3()) },
  uDir: { value: Array.from({ length: MAX_LIGHTS }, () => new THREE.Vector3(0, -1, 0)) },
  uCol: { value: Array.from({ length: MAX_LIGHTS }, () => new THREE.Vector3()) },
  // x = cos(outer), y = cos(inner), z = gobo id (0 = open), w = gobo angle
  uCone: { value: Array.from({ length: MAX_LIGHTS }, () => new THREE.Vector4(0.9, 0.95, 0, 0)) },
  uAmbient: { value: new THREE.Color(0x0b0d12) },
  uTime: { value: 0 },
};

// Gobo patterns, drawn analytically so no texture atlas is needed.  `uv`
// is the position inside the beam, -1..1 across its diameter.
const GOBO_GLSL = /* glsl */ `
float goboMask(float id, vec2 uv) {
  float r = length(uv);
  float a = atan(uv.y, uv.x);
  if (id < 0.5) return 1.0;
  if (id < 1.5) {                                   // dot ring
    float k = 0.0;
    for (int i = 0; i < 8; i++) {
      float t = float(i) * 0.785398;
      k = max(k, 1.0 - smoothstep(0.13, 0.17, length(uv - 0.58 * vec2(cos(t), sin(t)))));
    }
    return max(k, 1.0 - smoothstep(0.16, 0.2, r));
  }
  if (id < 2.5) {                                   // star
    float s = 0.35 + 0.45 * pow(abs(cos(a * 2.5)), 6.0);
    return 1.0 - smoothstep(s - 0.04, s + 0.04, r);
  }
  if (id < 3.5) {                                   // bars
    return smoothstep(0.35, 0.45, abs(fract(uv.x * 2.5) - 0.5) * 2.0);
  }
  if (id < 4.5) {                                   // rings
    return smoothstep(0.3, 0.5, abs(fract(r * 3.0) - 0.5) * 2.0);
  }
  if (id < 5.5) {                                   // petals
    return smoothstep(0.2, 0.35, abs(sin(a * 3.0)) * (1.0 - r * 0.6));
  }
  if (id < 6.5) {                                   // breakup
    vec2 p = uv * 3.2;
    float n = sin(p.x * 1.7 + sin(p.y * 2.3)) * cos(p.y * 1.3 + sin(p.x * 1.9));
    return smoothstep(-0.05, 0.25, n);
  }
  float c = min(abs(uv.x), abs(uv.y));              // cross
  return 1.0 - smoothstep(0.08, 0.12, c);
}
`;

const SURFACE_VERT = /* glsl */ `
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldPos = wp.xyz;
  vWorldNormal = normalize(mat3(modelMatrix) * normal);
  gl_Position = projectionMatrix * viewMatrix * wp;
}
`;

const SURFACE_FRAG = /* glsl */ `
#define MAX_LIGHTS ${MAX_LIGHTS}
uniform int uCount;
uniform vec3 uPos[MAX_LIGHTS];
uniform vec3 uDir[MAX_LIGHTS];
uniform vec3 uCol[MAX_LIGHTS];
uniform vec4 uCone[MAX_LIGHTS];
uniform vec3 uAmbient;
uniform vec3 uAlbedo;
uniform float uGrid;
uniform float uSheen;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
${GOBO_GLSL}
void main() {
  vec3 N = normalize(vWorldNormal);
  if (!gl_FrontFacing) N = -N;
  vec3 light = uAmbient * (0.6 + 0.4 * max(N.y, 0.0));
  for (int i = 0; i < MAX_LIGHTS; i++) {
    if (i >= uCount) break;
    vec3 L = vWorldPos - uPos[i];
    float d = length(L);
    vec3 Ld = L / max(d, 1e-4);
    float c = dot(Ld, uDir[i]);
    vec4 cone = uCone[i];
    if (c <= cone.x) continue;
    float spot = smoothstep(cone.x, cone.y, c);
    if (cone.z > 0.5) {
      vec3 dir = uDir[i];
      vec3 up = abs(dir.y) < 0.99 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);
      vec3 rt = normalize(cross(dir, up));
      vec3 u2 = cross(rt, dir);
      float t = dot(L, dir);
      float tanO = sqrt(max(1.0 - cone.x * cone.x, 1e-5)) / cone.x;
      vec2 uv = vec2(dot(L, rt), dot(L, u2)) / max(t * tanO, 1e-4);
      float cs = cos(cone.w), sn = sin(cone.w);
      uv = mat2(cs, -sn, sn, cs) * uv;
      spot *= goboMask(cone.z, uv);
    }
    float lam = max(dot(N, -Ld), 0.0);
    float att = 1.0 / (1.0 + 0.09 * d * d);
    light += uCol[i] * spot * lam * att;
  }
  vec3 albedo = uAlbedo;
  if (uGrid > 0.0) {                     // a faint metre grid on the deck
    vec2 g = abs(fract(vWorldPos.xz) - 0.5);
    float line = 1.0 - smoothstep(0.0, 0.012, 0.5 - max(g.x, g.y));
    albedo *= 1.0 + uGrid * line;
  }
  vec3 col = albedo * light;
  // a hint of a gloss floor: the brightest pools bloom a little
  col += uSheen * light * 0.04;
  gl_FragColor = vec4(col, 1.0);
}
`;

/** A diffuse surface that every beam in the rig lights. */
export function surfaceMaterial(albedo, opts = {}) {
  const uniforms = {
    ...LIGHTS,
    uAlbedo: { value: new THREE.Color(albedo) },
    uGrid: { value: opts.grid || 0 },
    uSheen: { value: opts.sheen || 0 },
  };
  return new THREE.ShaderMaterial({
    uniforms, vertexShader: SURFACE_VERT, fragmentShader: SURFACE_FRAG,
    side: opts.side || THREE.FrontSide,
  });
}

// ---------------------------------------------------------------------------
// Beams: an open cone, scaled in the vertex shader so one geometry serves
// every beam.  Additive, depth-tested (a fixture body hides the beam behind
// it), never depth-written (beams do not hide each other).
// ---------------------------------------------------------------------------
const BEAM_VERT = /* glsl */ `
uniform float uLen;
uniform float uR0;
uniform float uR1;
varying float vT;
varying vec3 vWorldPos;
varying vec3 vViewNormal;
varying vec3 vViewPos;
varying vec2 vRad;
void main() {
  float t = position.y;
  float r = mix(uR0, uR1, t);
  vec3 p = vec3(position.x * r, t * uLen, position.z * r);
  vT = t;
  vRad = position.xz;
  vec4 wp = modelMatrix * vec4(p, 1.0);
  vWorldPos = wp.xyz;
  vec4 vp = viewMatrix * wp;
  vViewPos = vp.xyz;
  vViewNormal = normalize(normalMatrix * vec3(position.x, 0.0, position.z));
  gl_Position = projectionMatrix * vp;
}
`;

const BEAM_FRAG = /* glsl */ `
uniform vec3 uColor;
uniform float uIntensity;
uniform float uSoft;
uniform float uHaze;
uniform float uTime;
uniform float uLen;
varying float vT;
varying vec3 vWorldPos;
varying vec3 vViewNormal;
varying vec3 vViewPos;
float hash(vec3 p) {
  p = fract(p * 0.3183099 + 0.1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float noise(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash(i + vec3(0, 0, 0)), hash(i + vec3(1, 0, 0)), f.x),
                 mix(hash(i + vec3(0, 1, 0)), hash(i + vec3(1, 1, 0)), f.x), f.y),
             mix(mix(hash(i + vec3(0, 0, 1)), hash(i + vec3(1, 0, 1)), f.x),
                 mix(hash(i + vec3(0, 1, 1)), hash(i + vec3(1, 1, 1)), f.x), f.y), f.z);
}
void main() {
  float facing = abs(dot(normalize(vViewNormal), normalize(-vViewPos)));
  float edge = pow(facing, uSoft);
  float dist = vT * uLen;
  float along = exp(-dist * 0.085) * smoothstep(0.0, 0.03, vT)
                * (1.0 - smoothstep(0.93, 1.0, vT) * 0.6);
  vec3 q = vWorldPos * 0.7 + vec3(uTime * 0.07, uTime * 0.03, uTime * 0.05);
  float haze = 0.55 + 0.45 * (noise(q) * 0.65 + noise(q * 2.3) * 0.35);
  float a = uIntensity * edge * along * mix(1.0, haze, 0.8) * uHaze;
  gl_FragColor = vec4(uColor * a, a);
}
`;

let coneGeometry = null;

/** The shared unit cone: unit circle in x/z, y from 0 (lens) to 1 (end). */
export function beamGeometry() {
  if (coneGeometry) return coneGeometry;
  const g = new THREE.CylinderGeometry(1, 1, 1, 40, 12, true);
  g.translate(0, 0.5, 0);
  coneGeometry = g;
  return g;
}

export function beamMaterial() {
  return new THREE.ShaderMaterial({
    uniforms: {
      uColor: { value: new THREE.Color(1, 1, 1) },
      uIntensity: { value: 0 },
      uSoft: { value: 1.6 },
      uHaze: { value: 0.6 },
      uTime: LIGHTS.uTime,
      uLen: { value: 5 },
      uR0: { value: 0.05 },
      uR1: { value: 1 },
    },
    vertexShader: BEAM_VERT,
    fragmentShader: BEAM_FRAG,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    side: THREE.DoubleSide,
  });
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
