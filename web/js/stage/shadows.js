// Shadows on the floor, the walls and the stage, and in the haze: the
// crowd, the performers, the objects and the stage deck block the beams.
// Every beam lighting a surface is analytic (materials.js), so shadows
// can't come for free: the brightest beams each get a depth picture of what
// stands in their way (one atlas of 4 x 4 cells), and the surface shader
// darkens whatever is further from the lens than that.
//
// Up to 16 lights (High; Medium 6).  A light keeps its cell while it stays
// among the brightest, and the pictures are drawn again only when a light
// with one moved or the room changed - a rig that holds its position (most
// of a show) costs nothing here.  How soft a light's shadow is follows its
// optics: a wash's soft, a spot's or a profile's crisp.
import * as THREE from "three";
import { LIGHTS, SHADOW_SLOTS } from "./materials.js";

export { SHADOW_SLOTS };
// meshes that cast a shadow carry this layer (the venue builder sets it)
export const CASTER_LAYER = 5;
const CELL = 512;                        // per beam; the atlas is 4 x 4 of them

export class Shadows {
  constructor(renderer, scene) {
    this.renderer = renderer;
    this.scene = scene;
    this.rt = new THREE.RenderTarget(CELL * 4, CELL * 4, { depthBuffer: true });
    this.rt.depthTexture = new THREE.DepthTexture(CELL * 4, CELL * 4, THREE.UnsignedIntType);
    this.rt.depthTexture.minFilter = this.rt.depthTexture.magFilter = THREE.NearestFilter;
    this.cams = Array.from({ length: SHADOW_SLOTS }, () => new THREE.PerspectiveCamera(60, 1, 0.3, 45));
    for (const c of this.cams) c.layers.set(CASTER_LAYER);
    // back faces only: the top of the deck or a bar is lit, its underside
    // is what a beam from above is stopped by (no speckle on its own top)
    this.blank = new THREE.MeshBasicMaterial({ colorWrite: false, side: THREE.BackSide });
    this._v = new THREE.Vector3();
    this.keys = new Array(SHADOW_SLOTS).fill(null);    // which light each cell is for
    this.sig = "";
    this.drawn = 0;                                     // pictures drawn (the checks count them)
    LIGHTS.uShadowMap.value = this.rt.depthTexture;
  }

  /**
   * lights: brightest first, each {key, pos, dir, cosO, soft}; the first
   * `slots` get a cell (light.slot is set: its cell, or -1).  `room`
   * changes whenever what casts a shadow does (a new room, the crowd shown
   * or hidden): every picture is drawn again then.
   */
  update(lights, slots, room = 0) {
    const n = Math.min(SHADOW_SLOTS, slots, lights.length);
    LIGHTS.uShadowCount.value = n;
    for (const l of lights) l.slot = -1;
    if (!n) { this.keys.fill(null); this.sig = ""; return; }
    const want = lights.slice(0, n);
    const keys = new Set(want.map((l) => l.key));
    for (let s = 0; s < SHADOW_SLOTS; s++) if (this.keys[s] !== null && !keys.has(this.keys[s])) this.keys[s] = null;
    for (const l of want) {
      let s = this.keys.indexOf(l.key);
      if (s < 0) { s = this.keys.indexOf(null); this.keys[s] = l.key; }
      l.slot = s;
      LIGHTS.uShadowSoft.value[s] = l.soft || 1;
    }
    const r = this.renderer;
    // the depth range the renderer uses (WebGPU: 0..1; its WebGL 2
    // fallback: -1..1 - known once it has started), so the surface shader
    // reads the pictures the way they were drawn.  The rows run top-down on
    // both: three.js's WebGL 2 side places viewports from the top and turns
    // render-target pictures over when they are read.  (Flipping them here
    // as well put every shadow on the wrong side of its light on WebGL 2.)
    const gpu = r.coordinateSystem === THREE.WebGPUCoordinateSystem;
    LIGHTS.uZ01.value = gpu ? 1 : 0;
    LIGHTS.uFlipY.value = 1;
    const f = (v) => v.toFixed(3);
    const sig = room + "|" + want.map((l) => `${l.slot}:${f(l.pos.x)},${f(l.pos.y)},${f(l.pos.z)},${f(l.dir.x)},${f(l.dir.y)},${f(l.dir.z)},${l.cosO.toFixed(4)},${(l.near || 0).toFixed(2)}`).join(";");
    if (sig === this.sig) return;                       // nothing moved: the pictures stand
    this.sig = sig;
    const s = this.scene;
    for (const c of this.cams) c.coordinateSystem = r.coordinateSystem;
    const keep = { target: r.getRenderTarget(), bg: s.background, over: s.overrideMaterial, auto: r.autoClear };
    s.background = null;
    s.overrideMaterial = this.blank;
    want.forEach((l, i) => {
      const slot = l.slot, cam = this.cams[slot];
      cam.fov = Math.min(150, Math.max(8, 2 * Math.acos(Math.min(1, l.cosO)) * 180 / Math.PI + 4));
      // a light inside something (under the deck, in the booth) sees from
      // where its beam comes out of it, as the beam does (hitDistance)
      cam.near = Math.max(0.3, l.near || 0);
      cam.position.copy(l.pos);
      cam.up.set(Math.abs(l.dir.y) < 0.99 ? 0 : 1, Math.abs(l.dir.y) < 0.99 ? 1 : 0, 0);
      cam.lookAt(this._v.copy(l.pos).add(l.dir));
      cam.updateProjectionMatrix();
      cam.updateMatrixWorld();
      LIGHTS.uShadowMat.value[slot].multiplyMatrices(cam.projectionMatrix, cam.matrixWorldInverse);
      const x = (slot % 4) * CELL, y = Math.floor(slot / 4) * CELL;
      this.rt.viewport.set(x, y, CELL, CELL);
      this.rt.scissor.set(x, y, CELL, CELL);
      this.rt.scissorTest = true;
      r.setRenderTarget(this.rt);
      // the first picture clears the whole atlas; the others draw beside it
      r.autoClear = i === 0;
      r.render(s, cam);
      this.drawn++;
    });
    r.setRenderTarget(keep.target);
    s.background = keep.bg;
    s.overrideMaterial = keep.over;
    r.autoClear = keep.auto;
  }

  off() {
    LIGHTS.uShadowCount.value = 0;
    this.keys.fill(null);
    this.sig = "";
  }

  dispose() { this.rt.dispose(); this.blank.dispose(); }
}
