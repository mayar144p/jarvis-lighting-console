// Special effects in the 3D view: confetti, CO2, flame, sparks, fog and
// laser fans.  Driven by the engine's look feed (`fx` on a head's row),
// which is exactly what the FX layer is sending the real machines - so a
// confetti burst on screen means the launcher's fan is on right now.
//
// Particles are plain THREE.Points with a colour+alpha attribute, one
// pool per effect head; a laser is a set of additive line segments fanned
// around the beam axis by its pattern, size and rotation channels.
import * as THREE from "three";

const CONFETTI = [0xff3b6b, 0xffd23b, 0x3bb8ff, 0x62ff8a, 0xff8a1a, 0xb45cff, 0xffffff];

const KINDS = {
  confetti: { n: 1400, rate: 420, life: [4, 6.5], speed: [6.5, 9.5], spread: 0.32, gravity: -3.2, drag: 1.4,
    size: 0.11, additive: false, flutter: 1.2, floor: true },
  co2: { n: 900, rate: 520, life: [0.45, 0.95], speed: [9, 12], spread: 0.07, gravity: 0.8, drag: 2.4,
    size: 0.55, additive: false, alpha: 0.28, colour: [0.92, 0.95, 1] },
  flame: { n: 700, rate: 420, life: [0.3, 0.65], speed: [4.5, 6.5], spread: 0.12, gravity: 2, drag: 1.2,
    size: 0.55, additive: true, alpha: 0.9, fire: true },
  spark: { n: 900, rate: 520, life: [0.7, 1.4], speed: [3.8, 5.6], spread: 0.14, gravity: -7, drag: 0.35,
    size: 0.06, additive: true, alpha: 1, colour: [1, 0.78, 0.35] },
  atmos: { n: 500, rate: 70, life: [5, 8], speed: [1.4, 2.4], spread: 0.45, gravity: 0.08, drag: 0.7,
    size: 1.6, additive: false, alpha: 0.11, colour: [0.8, 0.82, 0.86] },
};
KINDS.sfx = KINDS.spark;

const rand = (a, b) => a + Math.random() * (b - a);

class Pool {
  constructor(kind, scene) {
    const k = KINDS[kind] || KINDS.spark;
    this.k = k;
    this.n = k.n;
    this.pos = new Float32Array(this.n * 3).fill(-999);
    this.vel = new Float32Array(this.n * 3);
    this.life = new Float32Array(this.n);
    this.max = new Float32Array(this.n);
    this.col = new Float32Array(this.n * 4);
    this.seed = new Float32Array(this.n).map(() => Math.random() * 6.28);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute("color", new THREE.BufferAttribute(this.col, 4).setUsage(THREE.DynamicDrawUsage));
    const m = new THREE.PointsMaterial({
      size: k.size, vertexColors: true, transparent: true, depthWrite: false, sizeAttenuation: true,
      blending: k.additive ? THREE.AdditiveBlending : THREE.NormalBlending,
    });
    this.points = new THREE.Points(g, m);
    this.points.frustumCulled = false;
    scene.add(this.points);
    this.next = 0;
    this.carry = 0;
    this.alive = 0;
  }

  spawn(origin, dir, count, level) {
    const k = this.k;
    const u = new THREE.Vector3(), v = new THREE.Vector3();
    u.set(dir.y, -dir.x, 0);
    if (u.lengthSq() < 1e-6) u.set(1, 0, 0);
    u.normalize();
    v.crossVectors(dir, u).normalize();
    for (let c = 0; c < count; c++) {
      const i = this.next;
      this.next = (this.next + 1) % this.n;
      const a = Math.random() * Math.PI * 2, r = Math.sqrt(Math.random()) * k.spread;
      const d = dir.clone().addScaledVector(u, Math.cos(a) * r).addScaledVector(v, Math.sin(a) * r).normalize();
      const s = rand(k.speed[0], k.speed[1]) * (0.6 + 0.4 * level);
      this.pos.set([origin.x, origin.y, origin.z], i * 3);
      this.vel.set([d.x * s, d.y * s, d.z * s], i * 3);
      this.max[i] = this.life[i] = rand(k.life[0], k.life[1]);
      let rgb;
      if (k.fire) rgb = [1, rand(0.35, 0.75), rand(0.05, 0.2)];
      else if (k.colour) rgb = k.colour;
      else {
        const hex = CONFETTI[(Math.random() * CONFETTI.length) | 0];
        rgb = [((hex >> 16) & 255) / 255, ((hex >> 8) & 255) / 255, (hex & 255) / 255];
      }
      this.col.set([rgb[0], rgb[1], rgb[2], k.alpha ?? 1], i * 4);
    }
  }

  step(dt, time) {
    const k = this.k;
    let alive = 0;
    const damp = Math.max(0, 1 - k.drag * dt);
    for (let i = 0; i < this.n; i++) {
      if (this.life[i] <= 0) continue;
      this.life[i] -= dt;
      const j = i * 3;
      if (this.life[i] <= 0) { this.pos[j + 1] = -999; this.col[i * 4 + 3] = 0; continue; }
      alive++;
      if (k.floor && this.pos[j + 1] <= 0.02) {          // confetti settles on the floor
        this.vel[j] = this.vel[j + 1] = this.vel[j + 2] = 0;
      } else {
        this.vel[j + 1] += k.gravity * dt;
        this.vel[j] *= damp; this.vel[j + 1] *= damp; this.vel[j + 2] *= damp;
        if (k.flutter && this.vel[j + 1] < 0) {
          const s = this.seed[i];
          this.vel[j] += Math.sin(time * 3 + s) * k.flutter * dt;
          this.vel[j + 2] += Math.cos(time * 2.6 + s) * k.flutter * dt;
          this.vel[j + 1] = Math.max(this.vel[j + 1], -1.1);        // paper falls slowly
        }
        this.pos[j] += this.vel[j] * dt;
        this.pos[j + 1] = Math.max(0.015, this.pos[j + 1] + this.vel[j + 1] * dt);
        this.pos[j + 2] += this.vel[j + 2] * dt;
      }
      const t = this.life[i] / this.max[i];
      const base = k.alpha ?? 1;
      this.col[i * 4 + 3] = k.floor ? Math.min(1, t * 4) * base : base * Math.min(1, t * 1.5);
    }
    this.alive = alive;
    const g = this.points.geometry;
    g.attributes.position.needsUpdate = true;
    g.attributes.color.needsUpdate = true;
    return alive;
  }

  dispose(scene) {
    scene.remove(this.points);
    this.points.geometry.dispose();
    this.points.material.dispose();
  }
}

const RAYS = 28;

class LaserFan {
  constructor(scene) {
    this.pos = new Float32Array(RAYS * 2 * 3);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage));
    this.mat = new THREE.LineBasicMaterial({ color: 0x22ff44, transparent: true, opacity: 0.85,
      blending: THREE.AdditiveBlending, depthWrite: false });
    this.lines = new THREE.LineSegments(g, this.mat);
    this.lines.frustumCulled = false;
    scene.add(this.lines);
  }

  update(origin, dir, fx, time, reach) {
    this.lines.visible = true;
    this.mat.color.set(fx.hex || "#22ff44");
    const pattern = fx.pattern ?? 0.2, size = fx.size ?? 0.4, rot = fx.rot ?? 0;
    const n = 4 + Math.floor(pattern * (RAYS - 4));
    const spread = Math.tan((6 + size * 40) * Math.PI / 180);
    const spin = time * (rot - 0.5) * 4;
    const u = new THREE.Vector3(dir.y, -dir.x, 0);
    if (u.lengthSq() < 1e-6) u.set(1, 0, 0);
    u.normalize();
    const v = new THREE.Vector3().crossVectors(dir, u).normalize();
    const d = new THREE.Vector3();
    if (Array.isArray(fx.beams) && fx.beams.length) {
      // a beam bar: one fixed beam per diode, spread along the bar, the lit
      // ones only; the whole bar tilts with its motor (y)
      const nb = Math.min(fx.beams.length, RAYS);
      const fan = Math.tan(24 * Math.PI / 180);
      for (let i = 0; i < RAYS; i++) {
        const j = i * 6;
        if (i >= nb || !fx.beams[i]) { this.pos.fill(0, j, j + 6); continue; }
        const f = nb > 1 ? (i / (nb - 1)) * 2 - 1 : 0;
        d.copy(dir).addScaledVector(u, f * fan).addScaledVector(v, ((fx.y ?? 0.5) - 0.5) * 1.2).normalize();
        const len = reach(origin, d);
        this.pos.set([origin.x + u.x * f * 0.3, origin.y + u.y * f * 0.3, origin.z + u.z * f * 0.3,
          origin.x + d.x * len, origin.y + d.y * len, origin.z + d.z * len], j);
      }
      this.lines.geometry.attributes.position.needsUpdate = true;
      return;
    }
    const shape = pattern < 0.34 ? "fan" : pattern < 0.67 ? "cone" : "tunnel";
    for (let i = 0; i < RAYS; i++) {
      const j = i * 6;
      if (i >= n) { this.pos.fill(0, j, j + 6); continue; }
      const f = n > 1 ? i / (n - 1) : 0.5;
      let a, r;
      if (shape === "fan") { a = spin; r = (f * 2 - 1); }
      else if (shape === "cone") { a = spin + f * Math.PI * 2; r = 1; }
      else { a = spin * 1.7 + f * Math.PI * 2 * 3; r = 0.35 + 0.65 * ((i * 7) % 5) / 4; }
      d.copy(dir).addScaledVector(u, Math.cos(a) * r * spread).addScaledVector(v, Math.sin(a) * r * spread)
        .addScaledVector(u, ((fx.x ?? 0.5) - 0.5) * 0.6).addScaledVector(v, ((fx.y ?? 0.5) - 0.5) * 0.6).normalize();
      const len = reach(origin, d);
      this.pos.set([origin.x, origin.y, origin.z, origin.x + d.x * len, origin.y + d.y * len, origin.z + d.z * len], j);
    }
    this.lines.geometry.attributes.position.needsUpdate = true;
  }

  hide() { this.lines.visible = false; }

  dispose(scene) {
    scene.remove(this.lines);
    this.lines.geometry.dispose();
    this.mat.dispose();
  }
}

export class SfxSystem {
  constructor(scene) {
    this.scene = scene;
    this.pools = new Map();      // head -> Pool
    this.lasers = new Map();     // head -> LaserFan
    this._o = new THREE.Vector3();
    this._d = new THREE.Vector3();
  }

  /** One frame.  Returns true while anything is still moving. */
  update(dt, time, fixtures, reach) {
    let busy = false;
    const seen = new Set();
    for (const [head, inst] of fixtures) {
      const fx = inst.cur && inst.cur.fx;
      const type = inst.data.body && inst.data.body.type;
      seen.add(head);
      if (type === "laser") {
        let fan = this.lasers.get(head);
        const em = inst.sk.emitters && inst.sk.emitters[0];
        if (fx && fx.laser && em) {
          if (!fan) this.lasers.set(head, (fan = new LaserFan(this.scene)));
          em.node.getWorldPosition(this._o);
          this._d.copy(em.dir).transformDirection(em.node.matrixWorld).normalize();
          fan.update(this._o, this._d, fx, time, reach);
          busy = true;
        } else if (fan) fan.hide();
        continue;
      }
      const nozzle = inst.sk.nozzle;
      if (!nozzle || !KINDS[type]) continue;
      let pool = this.pools.get(head);
      const on = fx && (fx.fire || fx.fog > 0);
      if (!pool && !on) continue;
      if (!pool) this.pools.set(head, (pool = new Pool(type, this.scene)));
      if (on) {
        nozzle.getWorldPosition(this._o);
        const dir = nozzle.userData.dir || [0, 1, 0];
        this._d.set(dir[0], dir[1], dir[2]).transformDirection(nozzle.matrixWorld).normalize();
        const level = fx.fog > 0 ? fx.fog : 1;
        pool.carry += pool.k.rate * level * dt;
        const count = Math.floor(pool.carry);
        pool.carry -= count;
        if (count) pool.spawn(this._o, this._d, count, level);
      }
      if (pool.step(dt, time) > 0 || on) busy = true;
    }
    for (const [head, pool] of this.pools) if (!seen.has(head)) { pool.dispose(this.scene); this.pools.delete(head); }
    for (const [head, fan] of this.lasers) if (!seen.has(head)) { fan.dispose(this.scene); this.lasers.delete(head); }
    return busy;
  }
}
