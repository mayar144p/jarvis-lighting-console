// The haze in the room, from the hazers in the rig.
//
// With the Haze slider on Auto, the 3D's haze is the haze the desk is
// making: a hazer the desk runs fills the room over a few minutes (faster
// in a small room), thickest round the hazer at first - a cloud that rolls
// out from its nozzle and spreads - and when it stops the cloud thins in
// seconds and the room's haze settles over minutes.  The slider then says
// how thick the haze gets at most.  A rig with no hazer keeps the slider's
// haze.  Pure maths, no scene: the stage feeds it the hazers each frame
// and hands the result to the beams (beams.js VOL).
import * as THREE from "three";

export const FILL_M3_PER_MIN = 400;       // what one hazer at full puts out
export const SETTLE_S = 300;              // the room's haze halves in five minutes
export const MAX_PLUMES = 4;

export class Atmosphere {
  constructor() {
    this.level = 0;                       // the room's haze, 0..1
    this.plumes = new Map();              // head -> {str, age, from, dir}
  }

  /**
   * One step of `dt` seconds.  hazers: [{head, pos, dir, out}] - out 0..1,
   * what the desk sends each one now; volume: the room's in cubic metres.
   */
  step(hazers, volume, dt) {
    dt = Math.max(0, Math.min(1, dt));
    let feed = 0;
    for (const h of hazers) feed += Math.max(0, Math.min(1, h.out));
    const fill = FILL_M3_PER_MIN / 60 / Math.max(50, volume);
    this.level += (feed * fill * (1 - this.level) - this.level * Math.LN2 / SETTLE_S) * dt;
    this.level = Math.max(0, Math.min(1, this.level));
    const running = new Set();
    for (const h of hazers) {
      if (!(h.out > 0.01)) continue;
      running.add(h.head);
      let p = this.plumes.get(h.head);
      if (!p) this.plumes.set(h.head, (p = { str: 0, age: 0, from: h.pos.clone(), dir: h.dir.clone() }));
      p.from.copy(h.pos);
      p.dir.copy(h.dir);
      p.str += (h.out - p.str) * Math.min(1, dt / 3);       // builds up in a few seconds
      p.age += dt;
    }
    for (const [head, p] of this.plumes) {
      if (running.has(head)) continue;
      p.str *= Math.pow(0.5, dt / 12);                        // thins out in seconds
      p.age += dt;
      if (p.str < 0.01) this.plumes.delete(head);
    }
  }

  /** The clouds, strongest first: [x, y, z, radius] and their strength. */
  clouds(out = []) {
    const list = [...this.plumes.values()].sort((a, b) => b.str - a.str).slice(0, MAX_PLUMES);
    out.length = 0;
    for (const p of list) {
      // it rolls out from the nozzle and rises a little as it spreads
      const roll = Math.min(3, 0.6 * p.age), rise = Math.min(1.5, 0.15 * p.age);
      const c = new THREE.Vector3().copy(p.from).addScaledVector(p.dir, roll);
      c.y += rise;
      out.push({ pos: c, radius: 1.2 + Math.min(4, 0.45 * p.age), str: p.str });
    }
    return out;
  }

  /** Back to clear air (a new show, the Auto switch). */
  clear() {
    this.level = 0;
    this.plumes.clear();
  }
}
