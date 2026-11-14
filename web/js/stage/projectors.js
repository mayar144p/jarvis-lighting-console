// Projectors: a video or a picture thrown onto the room.
//
// A projector (Arrange -> + More -> Projector) shows what an LED screen can
// (the rig's colours, a clip or a picture from this computer or a link,
// NDI) - not on a screen of its own but onto whatever is in front of it: a
// cyc, a drape, the stage, a building, the crowd.  It is a light like the
// rig's, so it gets all they get (materials.js / beams.js): its picture
// lands on every surface (the shader reads the picture where a gobo would
// be), as bright as its lumens over its throw say, falling with distance,
// with the shadows of whatever stands in it, in the light map, and in haze
// as a pyramid of its own colours.
//
// throw ratio: how far it is over how wide the picture is (1.5: 3 m wide
// from 4.5 m); aspect: the picture's width over its height.
import * as THREE from "three";
import { PROJ, MAX_PROJ } from "./materials.js";
import { volumeBeamMaterial, volumeGeometry } from "./beams.js";

const DEG = Math.PI / 180;

/** How wide a projector throws, as tangents of its half angles across and
 *  up, and its lens's circle (the cone round the picture's corners). */
export function throwOf(throwRatio, aspect) {
  const tx = 0.5 / Math.max(0.2, +throwRatio || 1.5);
  const ty = tx / Math.max(0.2, +aspect || 16 / 9);
  const td = Math.hypot(tx, ty);
  return { tx, ty, td, cosO: Math.cos(Math.atan(td * 1.06)), cosI: Math.cos(Math.atan(td * 1.01)), tanO: td * 1.06 };
}

/** The light (candela, in the shader's units: CD_PER_UNIT) of `lm` lumens
 *  over a picture tx by ty (tangents of the half angles). */
export function projectorPower(lm, tx, ty) {
  const a = Math.atan(tx), b = Math.atan(ty);
  const omega = 4 * Math.asin(Math.sin(a) * Math.sin(b));       // the picture's solid angle
  return (Math.max(0, lm) / Math.max(1e-4, omega)) * 10.5 / 3000;
}

export class Projectors {
  constructor(beamScene) {
    this.beamScene = beamScene;
    this.list = [];                        // {o, lens, mat, beam: {vol, shadowSlot}, k}
    this._p = new THREE.Vector3();
    this._d = new THREE.Vector3();
    this._r = new THREE.Vector3();
    this._u = new THREE.Vector3();
  }

  /** A room was built: its projectors (venue.js: built.projectors). */
  setRoom(built) {
    for (const p of this.list) { this.beamScene.remove(p.beam.vol); p.beam.vol.material.dispose(); }
    this.list = ((built && built.projectors) || []).slice(0, MAX_PROJ).map((p, k) => {
      const vol = new THREE.Mesh(volumeGeometry(), volumeBeamMaterial());
      vol.frustumCulled = false;
      vol.visible = false;
      this.beamScene.add(vol);
      return { ...p, k, beam: { vol, shadowSlot: -1 }, avg: [1, 1, 1] };
    });
    for (let k = 0; k < MAX_PROJ; k++) PROJ.tex[k].value = PROJ.black;
  }

  /**
   * Each frame: the projectors as lights (pushed onto `lights`, as the rig's
   * are) and their beams in haze.  hit(origin, dir, cap) -> how far to the
   * first solid thing; haze: the room's haze now; volumetric: drawn as
   * light in the air (Medium, High).
   */
  update(lights, { hit, haze = 0, volumetric = false, lightMap = false } = {}) {
    for (const p of this.list) {
      const o = p.o;
      const map = p.mat && p.mat.map;
      const on = o.content !== "off" && map && (o.lm || 0) > 0;
      p.beam.vol.visible = false;
      if (!on) { PROJ.tex[p.k].value = PROJ.black; continue; }
      PROJ.tex[p.k].value = map;
      p.lens.updateWorldMatrix(true, false);
      const e = p.lens.matrixWorld.elements;
      const pos = this._p.set(e[12], e[13], e[14]);
      const dir = this._d.set(e[8], e[9], e[10]).normalize();       // the lens's +z
      const rt = this._r.crossVectors(dir, Math.abs(dir.y) < 0.99 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0)).normalize();
      const up = this._u.crossVectors(rt, dir);
      const t = throwOf(o.throw, o.aspect);
      const power = projectorPower(o.lm || 0, t.tx, t.ty);
      const len = hit ? hit(pos, dir, 40) : 40;
      const lum = 0.2126 * p.avg[0] + 0.7152 * p.avg[1] + 0.0722 * p.avg[2];
      lights.push({
        pos: pos.clone(), dir: dir.clone(), cosO: t.cosO, cosI: t.cosI, gobo: 200 + p.k, rot: 0, blur: 0, prism: 0, prot: 0, side: 0,
        r: power, g: power, b: power, cr: 1, cg: 1, cb: 1, share: (o.lm || 0) / 3000, weight: (o.lm || 0) / 3000 * Math.max(0.05, lum) * 3,
        rx: t.tx / t.tanO, ry: t.ty / t.tanO, tint: lum, beam: p.beam, head: "proj:" + o.id, key: "proj:" + o.id,
        reach: len < 39.5 ? len * 1.1 : 40, soft: 1, hit: null, hitAt: len,
      });
      if (!volumetric || lightMap || haze <= 0.01) continue;
      // in haze: a pyramid of its own colours (beams.js reads the picture)
      const v = p.beam.vol.material.uniforms;
      const vol = p.beam.vol;
      vol.visible = true;
      vol.position.copy(pos);
      vol.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(rt, dir, up));
      v.uOrigin.value.copy(pos);
      v.uAxis.value.copy(dir);
      v.uRight.value.copy(rt);
      v.uUp.value.copy(up);
      v.uR0.value = 0.03;
      v.uTan.value = t.tanO;
      v.uLen.value = Math.min(40, len * 1.05 + 0.1);
      v.uFade.value = len >= 39.5 ? 1 : 0;
      v.uColor.value.setRGB(1, 1, 1);
      v.uColor2.value.setRGB(1, 1, 1);
      v.uPower.value = (o.lm || 0) / 3000 * 0.35;
      v.uSoft.value = 0.96;
      v.uHaze.value = haze;
      v.uGobo.value = 0;
      v.uLook.value.set(0, 0, 0, 0);
      v.uProj.value = p.k;
      v.uRect.value.set(t.tx / t.tanO, t.ty / t.tanO);
    }
  }

  /** What each projector shows, averaged (its light's colour in the light
   *  meter and its share of the light list) - a few times a second. */
  sample() {
    const c = this._cv || (this._cv = document.createElement("canvas"));
    c.width = c.height = 4;
    const ctx = this._ctx || (this._ctx = c.getContext("2d", { willReadFrequently: true }));
    for (const p of this.list) {
      const u = (p.mat && p.mat.userData) || {};
      const src = u.canvas || (u.ndi && u.ndi.canvas) || u.video || (p.mat && p.mat.map && p.mat.map.image);
      if (!src) continue;
      try {
        if (src.videoWidth === 0 || src.width === 0) continue;
        ctx.drawImage(src, 0, 0, 4, 4);
        const px = ctx.getImageData(0, 0, 4, 4).data;
        let r = 0, g = 0, b = 0;
        for (let i = 0; i < px.length; i += 4) { r += px[i]; g += px[i + 1]; b += px[i + 2]; }
        const lin = (v) => { const x = v / 16 / 255; return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
        p.avg = [lin(r), lin(g), lin(b)];
      } catch (e) { /* another site's picture: unreadable, keep the last */ }
    }
  }

  dispose() {
    for (const p of this.list) { this.beamScene.remove(p.beam.vol); p.beam.vol.material.dispose(); }
    this.list = [];
  }
}

export { DEG };
