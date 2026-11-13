// The sky over an outdoor stage.
//
// A venue with no roof (the Outdoor template, a festival field) has the
// sky over it, and the sky changes how a show looks more than anything
// else: at night the beams own the dark; at sunset they fight a bright
// horizon; in daylight they hardly show at all - worth knowing before the
// 6 pm slot.  The sky is drawn behind everything (scene.backgroundNode,
// reflected in a shiny floor too) and lights the room (LIGHTS.uAmbient and
// the room's own lights): a night sky hardly at all, daylight a lot.
//
//   night     dark blue-black and stars
//   city      fewer stars, an orange glow along the horizon
//   dusk      deep blue overhead, the sun just set: orange and pink low down
//   overcast  flat grey cloud
//   day       blue, the sun high
//   picture:  the operator's own 360-degree photo (an equirectangular one,
//             twice as wide as it is high), "picture:upload:<id>" or a link
//
// `turn` (degrees) turns the sky round: where the sunset or the picture's
// view is.
import * as THREE from "three";
import {
  Fn, vec2, vec3, float, uniform, texture, positionWorldDirection, equirectUV, normalize, dot, max, min,
  mix, pow, smoothstep, clamp, floor, fract, length, atan, asin, hash, select, cos, sin, fwidth,
} from "three/tsl";

export const SKIES = ["night", "city", "dusk", "overcast", "day"];

const DEG = Math.PI / 180;
// Colours as they show at the standard exposure (linear).  A bright sky is
// really far brighter than a night one, and a camera (or an eye) facing it
// is set that much darker: `ev` is the exposure each sky takes, in stops -
// the sky, its light and the field look as here, and the rig's lights show
// as much less as they really do (a beam at noon hardly shows).  It is
// fixed for each sky, never following the lights: a fade-out still fades.
const LOOK = {
  night: { zen: [0.0015, 0.002, 0.007], hor: [0.008, 0.010, 0.022], stars: 1.0, sun: null,
    light: [0.004, 0.005, 0.012], room: 0 },
  city: { zen: [0.003, 0.003, 0.008], hor: [0.075, 0.042, 0.022], stars: 0.3, sun: null,
    light: [0.022, 0.017, 0.015], room: 0.05 },
  // (the sun: how high, and which way - 0 behind the stage, 180 behind
  // the audience - before `turn`)
  dusk: { zen: [0.006, 0.014, 0.075], hor: [0.55, 0.22, 0.07], stars: 0.2,
    sun: { elev: -3, az: 18, colour: [1.6, 0.55, 0.18], disc: 0, glow: 1.8, band: 0.12 }, light: [0.16, 0.10, 0.10], room: 0.35, ev: -0.5 },
  overcast: { zen: [0.26, 0.27, 0.29], hor: [0.36, 0.37, 0.39], stars: 0, sun: null,
    light: [1.1, 1.12, 1.18], room: 1.2, ev: -1.5 },
  day: { zen: [0.045, 0.12, 0.40], hor: [0.28, 0.38, 0.55], stars: 0,
    sun: { elev: 58, az: 205, colour: [30, 27, 22], disc: 1, glow: 1.0 }, light: [2.2, 2.3, 2.5], room: 2.0, ev: -2.5 },
};

/** What the sky lights the room with: {light: [r, g, b] (the surfaces'
 *  ambient), room: how much the room's own lights (truss, bodies)
 *  brighten, ev: the exposure it takes (stops; the light is as bright as
 *  that needs)}. */
export function skyLight(kind) {
  const k = LOOK[kind] || LOOK.night;
  const ev = k.ev || 0, s = Math.pow(2, -ev);
  return { light: k.light.map((v) => v * s), room: k.room * s, ev };
}

/** The way the sky is turned, as a direction's (x, z) turned by `turn`. */
const turned = (dir, c, s) => vec3(dir.x.mul(c).sub(dir.z.mul(s)), dir.y, dir.x.mul(s).add(dir.z.mul(c)));

/** Stars: one cell of the sky in 50 has one, of its own brightness,
 *  somewhere in it - a point a pixel or two across at any screen size (its
 *  size worked out from how much sky a pixel covers); fewer low down, where
 *  the air is thicker. */
const stars = Fn(([dir, amount]) => {
  const u = atan(dir.z, dir.x).mul(1 / (2 * Math.PI)).add(0.5);
  const v = asin(clamp(dir.y, -1, 1)).mul(1 / Math.PI).add(0.5);
  const g = vec2(u.mul(900), v.mul(450));
  const cell = floor(g);
  const id = cell.x.add(cell.y.mul(1931.0));
  const h = hash(id);
  const at = vec2(hash(id.add(17.0)), hash(id.add(43.0))).mul(0.6).add(0.2);
  const px = max(fwidth(g.y), 1e-4);                     // cells per pixel
  const d = length(fract(g).sub(at)).div(px);            // pixels from the star
  const lit = smoothstep(0.2, 1.4, d).oneMinus().mul(h.sub(0.98).div(0.02).max(0));
  const tint = mix(vec3(0.75, 0.82, 1.0), vec3(1.0, 0.92, 0.8), hash(id.add(91.0)));
  return tint.mul(lit).mul(amount).mul(smoothstep(0.02, 0.25, dir.y)).mul(1.4);
});

/**
 * The sky's node for scene.backgroundNode, and its uniforms.  kind: one of
 * SKIES, or a picture's texture (tex) for "picture".
 */
export function skyNode(kind, turn = 0, tex = null, ground = [0.03, 0.04, 0.025]) {
  const t = uniform(turn * DEG);
  const dir = turned(normalize(positionWorldDirection), cos(t), sin(t));
  if (kind === "picture" && tex) {
    return { node: texture(tex, equirectUV(dir)).rgb.mul(Math.pow(2, -(tex.userData.ev || 0))), turn: t };
  }
  const k = LOOK[kind] || LOOK.night;
  const gain = Math.pow(2, -(k.ev || 0));
  const node = Fn(() => {
    const up = dir.y;
    // the gradient: the horizon's colour low down, the zenith's high up;
    // below the horizon (beyond the field) a darker ground
    // (the horizon's colour lies low: within ten degrees or so of it)
    const high = pow(smoothstep(-0.02, 0.42, up), 0.45);
    const col = mix(vec3(...k.hor), vec3(...k.zen), high).toVar();
    // the ground far off, beyond the field: as the sky lights it, hazier
    // towards the horizon
    const far = vec3(...ground.map((g, i) => g * k.light[i] + k.hor[i] * 0.04));
    col.assign(select(up.lessThan(-0.02), mix(vec3(...k.hor).mul(0.5), far, smoothstep(-0.12, -0.02, up).oneMinus()), col));
    if (k.sun) {
      // the sun (turn moves it) and its glow round it; a set sun still
      // lights the horizon under it
      const e = k.sun.elev * DEG, az = k.sun.az * DEG;
      const sd = new THREE.Vector3(Math.sin(az) * Math.cos(e), Math.sin(e), -Math.cos(az) * Math.cos(e)).normalize();
      const c = max(dot(dir, vec3(sd.x, sd.y, sd.z)), 0);
      const glow = pow(c, 16).mul(0.3).add(pow(c, 90).mul(0.7)).mul(k.sun.glow);
      const band = smoothstep(-0.02, 0.22, up).oneMinus().mul(pow(c, 3)).mul(k.sun.glow * (k.sun.band ?? 0.3));
      const sunCol = vec3(...k.sun.colour);
      const lit = sunCol.mul(glow.add(band)).mul(0.25);
      const disc = k.sun.disc ? sunCol.mul(smoothstep(0.9994, 0.9997, c)) : vec3(0);
      col.addAssign(lit.add(disc).mul(select(up.lessThan(-0.02), float(0.25), float(1))));
    }
    if (k.stars) col.addAssign(stars(dir, float(k.stars)));
    return col;
  })();
  return { node: min(node.mul(gain), vec3(60 * gain)), turn: t };
}

/** The average colour of a picture (light, linear) - what a photo sky
 *  lights the room with. */
export function pictureLight(img) {
  try {
    const c = document.createElement("canvas");
    c.width = 32; c.height = 16;
    const g = c.getContext("2d", { willReadFrequently: true });
    g.drawImage(img, 0, 0, 32, 16);
    const d = g.getImageData(0, 0, 32, 8).data;          // the top half: the sky
    const lin = (v) => { const x = v / 255; return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
    let r = 0, gg = 0, b = 0;
    const n = d.length / 4;
    for (let i = 0; i < d.length; i += 4) { r += lin(d[i]); gg += lin(d[i + 1]); b += lin(d[i + 2]); }
    const k = 2.4;                                        // a bright photo lights the room like daylight
    const lum = (0.2126 * r + 0.7152 * gg + 0.0722 * b) / n;
    // a bright photo is a bright sky: exposed for it (as the skies above)
    const ev = lum > 0.25 ? -2.5 : lum > 0.1 ? -1.5 : lum > 0.04 ? -0.5 : 0;
    const s = Math.pow(2, -ev);
    return { light: [r / n * k * s, gg / n * k * s, b / n * k * s], room: Math.min(2.2, (r + gg + b) / n * 2) * s, ev };
  } catch (e) {
    return { light: [0.01, 0.01, 0.015], room: 0, ev: 0 };
  }
}
