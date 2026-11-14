// What a light's lens looks like (models.js lensMaterial).
//
// Dark, a stand-in's lens used to be a flat black disc - the light read as
// a hole.  A real one, unlit, is coated glass: dark with a blue-violet
// sheen, lighter towards its rim, the room's light caught in it; an LED
// cell shows the little die under its lens; a Fresnel or a profile's lens
// shows rings.  Lit, the lens is its light, brightest where the source is
// (the die, the lamp) - the same pictures as an emissive map.
import * as THREE from "three";

const cache = new Map();

function canvas(size, draw) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  draw(c.getContext("2d"), size);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** The lens as it looks: "glass" (a spot's front lens), "cell" (an LED
 *  under its own lens), "fresnel" (rings). */
export function lensLook(kind = "glass") {
  if (cache.has(kind)) return cache.get(kind);
  // (no page to draw on - the checks build bodies under Node: plain glass)
  if (typeof document === "undefined") return { map: null, emissiveMap: null };
  const tex = canvas(128, (g, s) => {
    const m = s / 2;
    // coated glass: near black, a cool sheen, a lighter rim
    const base = g.createRadialGradient(m, m, 0, m, m, m);
    base.addColorStop(0, "#07080d");
    base.addColorStop(0.72, "#0d1020");
    base.addColorStop(0.93, "#232842");
    base.addColorStop(1, "#3a3f55");
    g.fillStyle = base;
    g.fillRect(0, 0, s, s);
    if (kind === "cell") {
      // the LED die under its lens: a small pale square, its phosphor
      g.fillStyle = "#5c5a44";
      g.fillRect(m - s * 0.11, m - s * 0.11, s * 0.22, s * 0.22);
      g.fillStyle = "#b9b48a";
      g.fillRect(m - s * 0.07, m - s * 0.07, s * 0.14, s * 0.14);
    } else if (kind === "fresnel") {
      // the stepped rings of a Fresnel lens
      g.lineWidth = s * 0.018;
      for (let r = s * 0.08; r < m * 0.95; r += s * 0.055) {
        g.strokeStyle = "rgba(120, 130, 170, 0.28)";
        g.beginPath(); g.arc(m, m, r, 0, Math.PI * 2); g.stroke();
      }
    }
    // the room's light caught in the glass: a soft highlight up and left
    const hl = g.createRadialGradient(m * 0.62, m * 0.55, 0, m * 0.62, m * 0.55, m * 0.45);
    hl.addColorStop(0, "rgba(255,255,255,0.22)");
    hl.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = hl;
    g.fillRect(0, 0, s, s);
  });
  // lit: brightest where the light comes from
  const glow = canvas(64, (g, s) => {
    const m = s / 2;
    const r = g.createRadialGradient(m, m, 0, m, m, m);
    if (kind === "cell") {
      r.addColorStop(0, "#ffffff"); r.addColorStop(0.35, "#f2f2f2"); r.addColorStop(0.75, "#9a9a9a"); r.addColorStop(1, "#505050");
    } else {
      r.addColorStop(0, "#ffffff"); r.addColorStop(0.7, "#eeeeee"); r.addColorStop(1, "#8a8a8a");
    }
    g.fillStyle = r;
    g.fillRect(0, 0, s, s);
  });
  const look = { map: tex, emissiveMap: glow };
  cache.set(kind, look);
  return look;
}
