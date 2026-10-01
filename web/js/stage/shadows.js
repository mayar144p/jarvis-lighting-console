// Shadows on the floor, the walls and the stage: the crowd, the performers,
// the objects and the stage deck block the beams.  Every beam lighting a
// surface is analytic (materials.js), so shadows can't come for free: the
// brightest few beams each get a depth picture of what stands in their way
// (one atlas, a quarter each), and the surface shader darkens whatever is
// further from the lens than that.
import * as THREE from "three";
import { LIGHTS } from "./materials.js";

export const SHADOW_SLOTS = 4;
// meshes that cast a shadow carry this layer (the venue builder sets it)
export const CASTER_LAYER = 5;
const SIZE = 1024;                       // the atlas; 512 per beam

export class Shadows {
  constructor(renderer, scene) {
    this.renderer = renderer;
    this.scene = scene;
    this.rt = new THREE.WebGLRenderTarget(SIZE, SIZE, { depthBuffer: true });
    this.rt.depthTexture = new THREE.DepthTexture(SIZE, SIZE, THREE.UnsignedIntType);
    this.rt.depthTexture.minFilter = this.rt.depthTexture.magFilter = THREE.NearestFilter;
    this.cams = Array.from({ length: SHADOW_SLOTS }, () => new THREE.PerspectiveCamera(60, 1, 0.3, 45));
    for (const c of this.cams) c.layers.set(CASTER_LAYER);
    // back faces only: the top of the deck or a bar is lit, its underside
    // is what a beam from above is stopped by (no speckle on its own top)
    this.blank = new THREE.MeshBasicMaterial({ colorWrite: false, side: THREE.BackSide });
    this._v = new THREE.Vector3();
    LIGHTS.uShadowMap.value = this.rt.depthTexture;
  }

  /** Draw the depth pictures for the first `n` uploaded beams (they are
   *  sorted brightest first). */
  update(n) {
    n = Math.min(SHADOW_SLOTS, n);
    LIGHTS.uShadowCount.value = n;
    if (!n) return;
    const r = this.renderer, s = this.scene;
    const keep = { target: r.getRenderTarget(), bg: s.background, over: s.overrideMaterial, auto: r.autoClear };
    s.background = null;
    s.overrideMaterial = this.blank;
    r.autoClear = false;
    const half = SIZE / 2;
    for (let i = 0; i < n; i++) {
      const cam = this.cams[i];
      const pos = LIGHTS.uPos.value[i], dir = LIGHTS.uDir.value[i];
      const cosO = LIGHTS.uCone.value[i].x;
      cam.fov = Math.min(150, Math.max(8, 2 * Math.acos(Math.min(1, cosO)) * 180 / Math.PI + 4));
      cam.position.copy(pos);
      cam.up.set(Math.abs(dir.y) < 0.99 ? 0 : 1, Math.abs(dir.y) < 0.99 ? 1 : 0, 0);
      cam.lookAt(this._v.copy(pos).add(dir));
      cam.updateProjectionMatrix();
      cam.updateMatrixWorld();
      LIGHTS.uShadowMat.value[i].multiplyMatrices(cam.projectionMatrix, cam.matrixWorldInverse);
      const x = (i % 2) * half, y = Math.floor(i / 2) * half;
      this.rt.viewport.set(x, y, half, half);
      this.rt.scissor.set(x, y, half, half);
      this.rt.scissorTest = true;
      r.setRenderTarget(this.rt);
      r.clear(false, true, false);
      r.render(s, cam);
    }
    r.setRenderTarget(keep.target);
    s.background = keep.bg;
    s.overrideMaterial = keep.over;
    r.autoClear = keep.auto;
  }

  off() { LIGHTS.uShadowCount.value = 0; }

  dispose() { this.rt.dispose(); this.blank.dispose(); }
}
