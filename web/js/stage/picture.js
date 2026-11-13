// The picture's last step: from the light in the room (as bright as it
// really is - a lens can be many times brighter than the screen's white) to
// the colours the screen can show.
//
// Colour first: the colour on the desk is the colour on the screen.  The
// film-style curve used before (ACES) bent strong colours as they got
// brighter - bright red went orange, green went yellow-green, pink went
// red.  Here the same tonal curve (the same contrast and roll-off, so the
// room is as bright as it was) works on each colour's brightest channel and
// scales the colour as a whole, so its hue and saturation are kept.  Only
// far brighter than the screen's white - a lens, the core of a narrow beam -
// does a colour run to white, as it does to a camera and to the eye.
import * as THREE from "three";
import { Fn, vec3, vec4, max, clamp, smoothstep, mix, renderOutput, toneMappingExposure } from "three/tsl";

// the tonal curve (the RRT + ODT fit that three.js's ACES uses), 0..1 out
const curve = (c) => c.mul(c.add(0.0245786)).sub(0.000090537)
  .div(c.mul(c.mul(0.983729).add(0.4329510)).add(0.238081));

/** Linear light (exposure applied) -> linear colour the screen can show. */
export const trueColour = Fn(([colour]) => {
  const x = max(colour.rgb, vec3(0)).mul(toneMappingExposure.div(0.6));
  const peak = max(max(x.r, x.g), max(x.b, 1e-6)).toVar();
  const f = clamp(curve(peak), 0, 1).toVar();
  const rgb = x.mul(f.div(peak));
  // very bright: towards white (a lens, a beam's core)
  const w = smoothstep(2.5, 14, peak).mul(0.85);
  return vec4(mix(rgb, vec3(f), w), 1);
});

/** The node a render pipeline ends with: the true-colour curve, then the
 *  screen's colour space (the pipeline's own tone mapping must be off). */
export function pictureOut(colourNode) {
  return renderOutput(trueColour(colourNode), THREE.NoToneMapping, THREE.SRGBColorSpace);
}

// The standard exposure: mid-grey lands where the old curve put it.
export const BASE_EXPOSURE = 1.05;

/** The renderer's exposure for an exposure setting in stops (-2 .. +2). */
export function exposureFor(stops) {
  const s = Math.max(-3, Math.min(3, +stops || 0));
  return BASE_EXPOSURE * Math.pow(2, s);
}
