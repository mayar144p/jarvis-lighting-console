// The add-fixture dialog's turntable: one fixture, slowly turning.
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { buildFixture } from "./models.js";
import { buildGdtf } from "./gdtf.js";
import { rendererOpts } from "./gpu.js";

/** A single fixture on a turntable, for the add-fixture dialog. */
export class FixturePreview {
  constructor(container) {
    this.el = container;
    try {
      this.renderer = new THREE.WebGPURenderer(rendererOpts({ antialias: true, alpha: true }));
    } catch (e) {
      this.failed = true;
      return;
    }
    this.ready = false;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.renderer.init().then(() => {
      if (this.destroyed) return;
      const pmrem = new THREE.PMREMGenerator(this.renderer);
      this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
      this.ready = true;
    }).catch(() => { this.failed = true; });
    this.scene.add(new THREE.HemisphereLight(0xcfd8ea, 0x202028, 1.3));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(2, 3, 4);
    this.scene.add(key);
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.01, 50);
    this.pivot = new THREE.Group();
    this.scene.add(this.pivot);
    this._loop = this._loop.bind(this);
    this._raf = requestAnimationFrame(this._loop);
  }

  /** The drawn stand-in for this kind of light. */
  show(body, family = "") {
    if (this.failed) return;
    this.token = (this.token || 0) + 1;
    this._mount(buildFixture(body, family), body);
  }

  /** Nothing yet (the real 3D is on its way). */
  clear() {
    this.token = (this.token || 0) + 1;
    if (this.pivot) this.pivot.clear();
    this.sk = null;
  }

  /** The manufacturer's own 3D (a GDTF definition); false when it can't be
   *  built - then the caller shows the stand-in. */
  async showReal(def, body, family, fetchModel) {
    if (this.failed) return false;
    const token = this.token = (this.token || 0) + 1;
    const sk0 = buildFixture(body, family);
    const lensMat = sk0.lenses[0] || new THREE.MeshStandardMaterial();
    let housingMat = null;
    sk0.root.traverse((o) => {
      if (!housingMat && o.isMesh && o.material && o.material.isMeshStandardMaterial && o.material !== lensMat) housingMat = o.material;
    });
    const sk = await buildGdtf(def, body, fetchModel, housingMat || new THREE.MeshStandardMaterial({ color: 0x1b1c20 }), lensMat)
      .catch(() => null);
    if (!sk || token !== this.token || this.destroyed) return token === this.token ? false : true;
    this._mount(sk, body);
    return true;
  }

  _mount(sk, body) {
    this.pivot.clear();
    const moving = body && body.moving;
    if (!moving && !sk.standing && sk.tilt) sk.tilt.rotation.x = 0.5;
    for (const lens of sk.lenses || []) if (lens.emissive) lens.emissive.setRGB(0.9, 0.85, 0.7).multiplyScalar(1.5);
    this.pivot.add(sk.root);
    const box = new THREE.Box3().setFromObject(sk.root);
    const c = box.getCenter(new THREE.Vector3());
    sk.root.position.sub(c);
    const size = box.getSize(new THREE.Vector3()).length();
    this.camera.position.set(0, size * 0.35, size * 1.9);
    this.camera.lookAt(0, 0, 0);
    this.sk = sk;
  }

  _loop() {
    if (this.destroyed) return;
    this._raf = requestAnimationFrame(this._loop);
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (!w || !h || !this.ready) return;
    if (this.renderer.domElement.width !== Math.round(w * this.renderer.getPixelRatio())) {
      this.renderer.setSize(w, h, false);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
    }
    this.pivot.rotation.y += 0.008;
    if (this.sk && this.sk.pan && this.sk.moving !== false) {
      const t = performance.now() / 1000;
      if (this.sk.tilt && this.sk.pan && this.pivot.children.length) {
        this.sk.tilt.rotation.x = 0.5 + Math.sin(t * 0.9) * 0.35;
      }
    }
    this.renderer.render(this.scene, this.camera);
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this._raf);
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
    }
  }
}
