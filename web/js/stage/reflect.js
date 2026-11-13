// The floor as a mirror: a black dance floor, tiles or polished concrete
// shows the beams, the lenses, the truss and the screens reflected in it -
// in a club, half of what a beam looks like is its reflection.
//
// Each frame (Medium and High, a shiny floor, the camera above it) the
// room is drawn again from a camera mirrored under the floor, smaller than
// the screen (High half, Medium a quarter), and the beams in haze are
// drawn into that view as well, with the mirror view's own depth.  The
// floor's shader (materials.js, REFL) looks its point up in that picture -
// a rougher floor reads a blurrier mipmap level - stronger at a glancing
// angle than looking straight down.
import * as THREE from "three";
import { REFL } from "./materials.js";
import { VOL } from "./beams.js";

const EMPTY = new THREE.Scene();

export class FloorMirror {
  constructor() {
    this.cam = new THREE.PerspectiveCamera();
    this.rt = null;
    this.beamRT = null;
    this._p = new THREE.Vector3();
    this._f = new THREE.Vector3();
    this._u = new THREE.Vector3();
  }

  _targets(w, h) {
    const opts = { type: THREE.HalfFloatType, generateMipmaps: true, minFilter: THREE.LinearMipmapLinearFilter };
    if (!this.rt) {
      this.rt = new THREE.RenderTarget(w, h, { ...opts, depthBuffer: true });
      this.rt.depthTexture = new THREE.DepthTexture(w, h);
      this.rt.depthTexture.minFilter = this.rt.depthTexture.magFilter = THREE.NearestFilter;
      this.beamRT = new THREE.RenderTarget(1, 1, { ...opts, depthBuffer: false });
      REFL.room.value = this.rt.texture;
      REFL.beams.value = this.beamRT.texture;
    }
    if (this.rt.width !== w || this.rt.height !== h) this.rt.setSize(w, h);
    const bw = Math.max(1, Math.ceil(w / 2)), bh = Math.max(1, Math.ceil(h / 2));
    if (this.beamRT.width !== bw || this.beamRT.height !== bh) this.beamRT.setSize(bw, bh);
  }

  /**
   * Draw the mirror for the floor (y = 0) as `camera` sees it.  `size`:
   * the picture's size in pixels; `beams`: also the beams in haze (their
   * scene); `hide`: what mustn't be in it (the floor itself).
   */
  render(renderer, scene, beamScene, camera, size, { beams = true, hide = [], steps = 8 } = {}) {
    const w = Math.max(1, Math.floor(size.x)), h = Math.max(1, Math.floor(size.y));
    this._targets(w, h);
    // the camera mirrored under the floor: where it is, which way it looks
    // and which way is up, all turned over at y = 0
    camera.updateMatrixWorld();
    const e = camera.matrixWorld.elements;
    const p = this._p.set(e[12], -e[13], e[14]);
    const f = this._f.set(-e[8], e[9], -e[10]);
    const u = this._u.set(e[4], -e[5], e[6]);
    const cam = this.cam;
    cam.fov = camera.fov;
    cam.aspect = camera.aspect;
    cam.near = camera.near;
    cam.far = camera.far;
    cam.zoom = camera.zoom;
    cam.coordinateSystem = camera.coordinateSystem;
    cam.position.copy(p);
    cam.up.copy(u);
    cam.lookAt(p.x + f.x, p.y + f.y, p.z + f.z);
    cam.updateProjectionMatrix();
    cam.updateMatrixWorld();
    REFL.matrix.value.multiplyMatrices(cam.projectionMatrix, cam.matrixWorldInverse);
    const shown = hide.map((m) => m.visible);
    for (const m of hide) m.visible = false;
    const keep = renderer.getRenderTarget();
    REFL.on.value = 0;                          // (the floor isn't in its own mirror)
    renderer.setRenderTarget(this.rt);
    renderer.render(scene, cam);
    if (beams) {
      const depth = VOL.depth.value, n = VOL.steps.value;
      VOL.depth.value = this.rt.depthTexture;
      VOL.steps.value = steps;
      const clear = renderer.getClearColor(new THREE.Color()), alpha = renderer.getClearAlpha();
      renderer.setClearColor(0x000000, 0);
      renderer.setRenderTarget(this.beamRT);
      renderer.render(beamScene, cam);
      renderer.setClearColor(clear, alpha);
      VOL.depth.value = depth;
      VOL.steps.value = n;
    } else {
      // nothing lit in the haze: an empty picture - drawn, not just cleared,
      // so its blurrier levels are made again too (a rough floor reads
      // those, and kept showing the beams of a moment ago)
      const clear = renderer.getClearColor(new THREE.Color()), alpha = renderer.getClearAlpha();
      renderer.setClearColor(0x000000, 0);
      renderer.setRenderTarget(this.beamRT);
      renderer.render(EMPTY, cam);
      renderer.setClearColor(clear, alpha);
    }
    renderer.setRenderTarget(keep);
    hide.forEach((m, i) => { m.visible = shown[i]; });
    REFL.on.value = 1;
  }

  off() { REFL.on.value = 0; }

  dispose() {
    if (this.rt) { this.rt.dispose(); this.beamRT.dispose(); }
  }
}
