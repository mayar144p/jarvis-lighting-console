// Arrange mode: move lights, rigging, objects, zones and the floor plan
// with a 3D gizmo, and draw the room outline and zones by clicking on the
// floor.
//
// Nothing here writes to the engine directly.  The editor previews a move
// live in the scene and reports it through `hooks` when the drag ends; the
// app turns that into one undoable engine action, and the next snapshot
// redraws the room from the engine's truth.
import * as THREE from "three";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { ceilingAt } from "./venue.js";

const SNAP_REACH = 0.6;

function nearestOnRig(rigs, p) {
  let best = null;
  for (const r of rigs) {
    const a = new THREE.Vector3(...r.a), b = new THREE.Vector3(...r.b);
    const ab = b.clone().sub(a);
    const len2 = ab.lengthSq();
    const t = len2 < 1e-9 ? 0 : Math.max(0, Math.min(1, p.clone().sub(a).dot(ab) / len2));
    const q = a.clone().addScaledVector(ab, t);
    const d = q.distanceTo(p);
    if (d <= SNAP_REACH + (r.size || 0.3) / 2 && (!best || d < best.d)) best = { rig: r, t, d, point: q };
  }
  return best;
}

// rig ends closer than this join end to end
const JOIN_M = 0.4;

// drafted objects made from two clicks
const TWO_CLICK = { door: 1, balcony: 1 };

// The heading of a turned object, from its quaternion.  Read in YXZ order:
// three.js keeps .rotation in XYZ, where a turn past 90 degrees comes back
// as a smaller angle with X and Z flipped - a truss turned 120 degrees
// was saved at 60 (it "snapped back").  A drag on the gizmo's outer ring
// (it turns about the view, not about up) can tilt the proxy: only the
// heading counts for anything that turns flat.
const _eul = new THREE.Euler();
const yawOf = (o) => _eul.setFromQuaternion(o.quaternion, "YXZ").y;

export class VenueEditor {
  constructor(stage, hooks = {}) {
    this.stage = stage;
    this.hooks = hooks;
    this.sel = null;                     // {type, id|head, ...}
    this.mode = "translate";
    this.draw = null;                    // {kind, points, preview}
    this.busy = false;

    const tc = new TransformControls(stage.camera, stage.renderer.domElement);
    tc.setTranslationSnap(0.05);
    tc.setRotationSnap(THREE.MathUtils.degToRad(15));
    tc.setSize(0.85);
    tc.addEventListener("dragging-changed", (e) => {
      stage.controls.enabled = !e.value;
      if (e.value) {
        this.busy = true;
        this._begin();
      } else {
        this._end();
        setTimeout(() => { this.busy = false; }, 0);
      }
    });
    tc.addEventListener("objectChange", () => this._preview());
    tc.addEventListener("change", () => { stage.dirty = true; });
    this.tc = tc;
    this.helper = tc.getHelper();
    this.helper.visible = false;
    stage.scene.add(this.helper);

    this.proxy = new THREE.Object3D();
    stage.scene.add(this.proxy);
    this.handles = new THREE.Group();
    stage.scene.add(this.handles);
    this.overlay = new THREE.Group();          // snap marker, drawing preview
    stage.scene.add(this.overlay);
    this.snapMark = new THREE.Mesh(new THREE.TorusGeometry(0.28, 0.03, 8, 32),
      new THREE.MeshBasicMaterial({ color: 0x22c55e, toneMapped: false }));
    this.snapMark.visible = false;
    this.overlay.add(this.snapMark);
    this.handleMat = new THREE.MeshBasicMaterial({ color: 0xfbbf24, toneMapped: false, depthTest: false });
    this.handleGeo = new THREE.SphereGeometry(0.14, 16, 12);
    this.raycaster = new THREE.Raycaster();
    this.enabled = false;
    // Shift+click: several rigs / objects picked to line up (no gizmo)
    this.multi = [];
    this.multiMarks = new THREE.Group();
    this.overlay.add(this.multiMarks);
    this.multiMat = new THREE.MeshBasicMaterial({ color: 0x60a5fa, toneMapped: false, depthTest: false });
  }

  // ------------------------------------------------------------ lifecycle
  setEnabled(on) {
    this.enabled = !!on;
    if (!on) {
      this.cancelDraw();
      this.clearMulti();
      this.clear();
    }
  }

  setMode(mode) {
    this.mode = mode === "rotate" ? "rotate" : "translate";
    this._applyMode();
  }

  _applyMode() {
    const s = this.sel;
    const canRotate = s && (s.type === "object" || s.type === "underlay" || s.type === "light" || s.type === "rig");
    const mode = this.mode === "rotate" && canRotate ? "rotate" : "translate";
    this.tc.setMode(mode);
    this.tc.showX = true; this.tc.showZ = true;
    this.tc.showY = true;
    if (mode === "rotate") {
      this.tc.showX = s.type === "light";           // a rig turns flat (seen from above); stand it up from the panel
      this.tc.showZ = false;
    } else if (s && (s.type === "zone" || s.type === "underlay" || s.type === "stage" || s.type === "vertex")) {
      this.tc.showY = false;                    // these live on the floor
    }
    this.stage.dirty = true;
  }

  /** The room's inside, as the engine clamps it (venue.py bounds + margin). */
  _roomBox() {
    const room = this.venue.room || {};
    const w = +room.width || 0, d = +room.depth || 0, h = +room.height || 0;
    if (!w || !d) return null;
    const m = 0.2, cx = +room.cx || 0, back = +room.back || 0;
    return { x0: cx - w / 2 + m, x1: cx + w / 2 - m, z0: back + m, z1: back + d - m, top: (h || 20) - 0.1, h: h || 20 };
  }

  /** A rig at the edge of the screen (or with the camera level with it,
   *  as in a low room) can't be grabbed: bring it into view first. */
  frameRigById(id) {
    const found = this._item(id);
    if (found && found.key === "rigging") this._frameRig(new THREE.Vector3(...found.it.a), new THREE.Vector3(...found.it.b), true);
  }

  _frameRig(a, b, force = false) {
    const st = this.stage, cam = st.camera;
    const mid = a.clone().add(b).multiplyScalar(0.5);
    const q = mid.clone().project(cam);
    const edge = Math.abs(q.x) > 0.75 || Math.abs(q.y) > 0.7 || q.z > 1;
    const flat = Math.abs(cam.position.y - mid.y) < 1.2;
    if (!force && !edge && !flat) return;
    const len = a.distanceTo(b);
    const box = this._roomBox();
    const back = Math.max(3, len * 0.9);
    const pos = new THREE.Vector3(mid.x, Math.max(0.6, mid.y - Math.min(1.6, mid.y * 0.55)), mid.z + back);
    if (box) pos.z = Math.min(pos.z, box.z1 + 3);       // may look in from outside the front wall
    st._flyTo(pos, mid, false);
  }

  /** The venue as currently drawn (the engine's, or the auto room). */
  get venue() { return (this.stage.built && this.stage.built.venue) || {}; }

  clear() {
    this.tc.detach();
    this.tc.axis = null;
    this.helper.visible = false;
    this.handles.clear();
    this.snapMark.visible = false;
    this.sel = null;
    this.stage.dirty = true;
  }

  /** Re-attach after the room was rebuilt from a new snapshot. */
  refresh() {
    if (this.multi.length) {
      this.multi = this.multi.filter((id) => this._item(id));
      this._drawMulti();
      if (this.hooks.onMulti) this.hooks.onMulti(this.multi);
    }
    if (!this.sel) return;
    const s = this.sel;
    if (s.type === "light") {
      if (!this.stage.fixtures.has(s.head)) return this.clear();
    } else if (s.type === "item" && !this._item(s.id)) {
      return this.clear();
    }
    this.select(s, { silent: true });
  }

  _item(id) {
    const v = this.venue;
    for (const key of ["rigging", "objects", "zones"]) {
      const it = (v[key] || []).find((x) => x.id === id);
      if (it) return { key, it };
    }
    return null;
  }

  // ------------------------------------------------------------ several
  /** Shift+click: add a rig or object to the picked set, or take it out. */
  toggleMulti(id) {
    const s = this.sel;
    if (!this.multi.length && s && (s.type === "rig" || s.type === "object") && s.id !== id) this.multi.push(s.id);
    const i = this.multi.indexOf(id);
    if (i >= 0) this.multi.splice(i, 1); else this.multi.push(id);
    this.select(null, { silent: true, keepMulti: true });
    this._drawMulti();
    if (this.hooks.onMulti) this.hooks.onMulti(this.multi);
  }

  clearMulti() {
    if (!this.multi.length) return;
    this.multi = [];
    this._drawMulti();
    if (this.hooks.onMulti) this.hooks.onMulti([]);
  }

  _drawMulti() {
    this.multiMarks.clear();
    for (const id of this.multi) {
      const f = this._item(id);
      if (!f) continue;
      const it = f.it;
      const pos = f.key === "rigging" ? new THREE.Vector3(...it.a).add(new THREE.Vector3(...it.b)).multiplyScalar(0.5)
        : new THREE.Vector3(it.x, (it.y || 0) + (it.h || 0) + 0.15, it.z);
      const m = new THREE.Mesh(this.handleGeo, this.multiMat);
      m.position.copy(pos);
      m.scale.setScalar(1.4);
      m.renderOrder = 11;
      this.multiMarks.add(m);
      if (f.key === "rigging") {
        const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...it.a), new THREE.Vector3(...it.b)]),
          new THREE.LineBasicMaterial({ color: 0x60a5fa, toneMapped: false, depthTest: false }));
        line.renderOrder = 11;
        this.multiMarks.add(line);
      }
    }
    this.stage.dirty = true;
  }

  // ------------------------------------------------------------ selection
  select(sel, opts = {}) {
    if (!opts.keepMulti) this.clearMulti();
    this.handles.clear();
    this.snapMark.visible = false;
    this.sel = sel;
    if (!sel) { this.clear(); if (!opts.silent && this.hooks.onSelect) this.hooks.onSelect(null); return; }
    const p = this.proxy;
    p.rotation.set(0, 0, 0);
    p.scale.set(1, 1, 1);
    if (sel.type === "light") {
      const inst = this.stage.fixtures.get(sel.head);
      if (!inst) return this.clear();
      p.position.copy(inst.holder.position);
      const rot = inst.data.rot;
      if (Array.isArray(rot)) p.rotation.set(-rot[1] * Math.PI / 180, rot[0] * Math.PI / 180, 0, "YXZ");
      sel.moving = !!(inst.data.body && inst.data.body.moving);
    } else if (sel.type === "item") {
      const found = this._item(sel.id);
      if (!found) return this.clear();
      sel.key = found.key;
      const it = found.it;
      if (found.key === "rigging") {
        sel.type = "rig";
        const a = new THREE.Vector3(...it.a), b = new THREE.Vector3(...it.b);
        p.position.copy(a).add(b).multiplyScalar(0.5);
        this._handle("a", a);
        this._handle("b", b);
        if (!opts.silent) this._frameRig(a, b);
      } else if (found.key === "objects") {
        sel.type = "object";
        p.position.set(it.x, it.y || 0, it.z);
        p.rotation.y = -(it.rot || 0) * Math.PI / 180;
      } else {
        sel.type = "zone";
        const cx = it.points.reduce((a, q) => a + q[0], 0) / it.points.length;
        const cz = it.points.reduce((a, q) => a + q[1], 0) / it.points.length;
        p.position.set(cx, (it.y || 0) + 0.02, cz);
        it.points.forEach((q, i) => this._handle(i, new THREE.Vector3(q[0], (it.y || 0) + 0.02, q[1])));
      }
      sel.item = JSON.parse(JSON.stringify(it));
    } else if (sel.type === "stage") {
      const st = this.venue.stage;
      if (!st) return this.clear();
      p.position.set(st.x, st.height, st.z + st.depth / 2);
    } else if (sel.type === "underlay") {
      const u = this.venue.underlay;
      if (!u) return this.clear();
      p.position.set(u.x, 0.01, u.z);
      p.rotation.y = -(u.rot || 0) * Math.PI / 180;
    }
    this.tc.attach(p);
    this.helper.visible = true;
    this._applyMode();
    if (!opts.silent && this.hooks.onSelect) this.hooks.onSelect(this.sel);
  }

  _handle(key, pos) {
    const m = new THREE.Mesh(this.handleGeo, this.handleMat);
    m.position.copy(pos);
    m.renderOrder = 10;
    m.userData.handle = key;
    this.handles.add(m);
  }

  selectHandle(key) {
    const s = this.sel;
    if (!s || (s.type !== "rig" && s.type !== "zone" && s.type !== "vertex")) return;
    const base = s.type === "vertex" ? s.parent : s;
    const h = this.handles.children.find((m) => m.userData.handle === key);
    if (!h) return;
    this.sel = { type: "vertex", parent: base, key, item: base.item, id: base.id, key0: base.key };
    this.proxy.position.copy(h.position);
    this.proxy.rotation.set(0, 0, 0);
    this.tc.attach(this.proxy);
    this.tc.showY = base.type === "rig";
    this.stage.dirty = true;
  }

  /** A rig turned about its middle by `yaw` (radians, about up), then slid
   *  back inside the room as the engine does (venue.keep_rig_inside):
   *  {a, b, mid, shift} - the new ends and the slide. */
  _turnRig(it, yaw) {
    const Y = new THREE.Vector3(0, 1, 0);
    const A = new THREE.Vector3(...it.a), B = new THREE.Vector3(...it.b);
    const mid = A.clone().add(B).multiplyScalar(0.5);
    const a = A.sub(mid).applyAxisAngle(Y, yaw).add(mid), b = B.sub(mid).applyAxisAngle(Y, yaw).add(mid);
    const shift = new THREE.Vector3();
    const box = this._roomBox();
    if (box) {
      const slide = (lo, hi, u, v) => (Math.min(u, v) < lo ? lo - Math.min(u, v) : Math.max(u, v) > hi ? hi - Math.max(u, v) : 0);
      shift.x = slide(box.x0, box.x1, a.x, b.x);
      shift.z = slide(box.z0, box.z1, a.z, b.z);
    }
    return { a: a.add(shift), b: b.add(shift), mid, shift };
  }

  /** Several rigs (a shape's pieces) turned together about their common
   *  middle, then slid inside the room as one: {parts, ends: [{a, b}],
   *  mid, shift}. */
  _turnShape(parts, yaw) {
    const Y = new THREE.Vector3(0, 1, 0);
    const pts = parts.flatMap((m) => [new THREE.Vector3(...m.it.a), new THREE.Vector3(...m.it.b)]);
    const mid = pts.reduce((acc, q) => acc.add(q), new THREE.Vector3()).multiplyScalar(1 / pts.length);
    const ends = parts.map((m) => ({ a: new THREE.Vector3(...m.it.a).sub(mid).applyAxisAngle(Y, yaw).add(mid),
      b: new THREE.Vector3(...m.it.b).sub(mid).applyAxisAngle(Y, yaw).add(mid) }));
    const shift = new THREE.Vector3();
    const box = this._roomBox();
    if (box) {
      const xs = ends.flatMap((e) => [e.a.x, e.b.x]), zs = ends.flatMap((e) => [e.a.z, e.b.z]);
      const slide = (lo, hi, vals) => (Math.min(...vals) < lo ? lo - Math.min(...vals) : Math.max(...vals) > hi ? hi - Math.max(...vals) : 0);
      shift.x = slide(box.x0, box.x1, xs);
      shift.z = slide(box.z0, box.z1, zs);
    }
    for (const e of ends) { e.a.add(shift); e.b.add(shift); }
    return { parts, ends, mid, shift };
  }

  /** A rig drag, kept inside the room and under its ceiling; near the
   *  ceiling it snaps up to hang just below it. */
  _rigDelta(it, d) {
    const dd = d.clone();
    const box = this._roomBox();
    if (!box) return dd;
    const xs = [it.a[0], it.b[0]], zs = [it.a[2], it.b[2]], ys = [it.a[1], it.b[1]];
    const fit = (lo, hi, vals, v) => {
      const mn = Math.min(...vals) + v, mx = Math.max(...vals) + v;
      if (mx - mn > hi - lo) return (lo + hi) / 2 - (Math.min(...vals) + Math.max(...vals)) / 2;   // longer than the room: centre it
      if (mn < lo) return v + (lo - mn);
      if (mx > hi) return v - (mx - hi);
      return v;
    };
    dd.x = fit(box.x0, box.x1, xs, dd.x);
    dd.z = fit(box.z0, box.z1, zs, dd.z);
    const size = +(it.size || 0.3);
    // just under the ceiling - a ceiling area's own where it is over the rig
    const ends = [[it.a[0] + dd.x, it.a[2] + dd.z], [it.b[0] + dd.x, it.b[2] + dd.z]];
    ends.push([(ends[0][0] + ends[1][0]) / 2, (ends[0][1] + ends[1][1]) / 2]);
    const ceil = Math.min(...ends.map(([x, z]) => ceilingAt(this.venue, x, z, box.h)));
    const hangY = ceil - size / 2 - 0.05;
    const top = Math.max(...ys) + dd.y;
    if (top > hangY - 0.35) dd.y = hangY - Math.max(...ys);  // snap up to the ceiling
    dd.y = Math.max(dd.y, -Math.min(...ys));                // never through the floor
    // end to end: an end near another piece's end joins it
    const j = this._joint([it.a, it.b].map((e) => new THREE.Vector3(...e).add(dd)), it.id);
    if (j) dd.add(j.shift);
    this._showJoint(j);
    return dd;
  }

  /** The nearest other rig end within JOIN_M of any of `ends`:
   *  {shift, point} - how far to move so they meet. */
  _joint(ends, selfId) {
    let best = null;
    for (const r of this.venue.rigging || []) {
      if (r.id === selfId) continue;
      for (const e of [r.a, r.b]) {
        const q = new THREE.Vector3(...e);
        for (const p of ends) {
          const dist = p.distanceTo(q);
          if (dist < JOIN_M && (!best || dist < best.dist)) best = { dist, shift: q.clone().sub(p), point: q };
        }
      }
    }
    return best;
  }

  _showJoint(j) {
    this.snapMark.visible = !!j;
    if (j) {
      this.snapMark.position.copy(j.point);
      this.snapMark.lookAt(this.stage.camera.position);
    }
  }

  // ------------------------------------------------------------ dragging
  _begin() {
    const s = this.sel;
    if (!s) return;
    s.start = this.proxy.position.clone();
    s.startRot = this.proxy.rotation.clone();
    s.startYaw = yawOf(this.proxy);
    if (s.type === "rig") {
      s.riders = [...this.stage.fixtures.values()].filter((i) => (i.data.mount || {}).rig === s.id)
        .map((i) => ({ inst: i, pos: i.holder.position.clone() }));
      const g = this.stage.built.items.get(s.id);
      s.group = g;
      s.group0 = g ? g.position.clone() : null;
      // a piece of a shape (circle, frame...): a move takes the whole shape
      // (venue_update moves the rest), so the preview shows it too
      const tag = s.item && s.item.group;
      s.mates = !tag ? [] : (this.venue.rigging || []).filter((r) => r.group === tag && r.id !== s.id).map((r) => {
        const mg = this.stage.built.items.get(r.id);
        return { id: r.id, it: JSON.parse(JSON.stringify(r)), g: mg, g0: mg ? mg.position.clone() : null,
          riders: [...this.stage.fixtures.values()].filter((i) => (i.data.mount || {}).rig === r.id)
            .map((i) => ({ inst: i, pos: i.holder.position.clone() })) };
      }).filter((m) => m.g);
    } else if (s.type === "object") {
      const g = this.stage.built.items.get(s.id);
      s.group = g;
      s.group0 = g ? g.position.clone() : null;
      s.groupRot0 = g ? g.rotation.y : 0;
    } else if (s.type === "light") {
      const inst = this.stage.fixtures.get(s.head);
      s.others = (this.hooks.selectedHeads ? this.hooks.selectedHeads() : [s.head])
        .filter((h) => h !== s.head).map((h) => this.stage.fixtures.get(h)).filter(Boolean)
        .map((i) => ({ inst: i, pos: i.holder.position.clone() }));
      s.inst = inst;
    }
  }

  _preview() {
    const s = this.sel;
    if (!s || !s.start) return;
    const d = this.proxy.position.clone().sub(s.start);
    if (s.type === "light") {
      const inst = s.inst;
      inst.data.x = this.proxy.position.x;
      inst.data.y = Math.max(0, this.proxy.position.y);
      inst.data.z = this.proxy.position.z;
      if (this.tc.mode === "rotate") {
        const e = new THREE.Euler().setFromQuaternion(this.proxy.quaternion, "YXZ");
        inst.data.rot = [Math.round(e.y * 180 / Math.PI), Math.round(-e.x * 180 / Math.PI)];
        this.stage._aimStatic(inst);
      } else {
        this.stage._place(inst);
        this.stage._aimStatic(inst);
        for (const o of s.others) {
          o.inst.holder.position.copy(o.pos).add(d);
        }
        const snap = nearestOnRig(this.venue.rigging || [], this.proxy.position);
        this.snapMark.visible = !!snap;
        if (snap) {
          this.snapMark.position.copy(snap.point);
          this.snapMark.lookAt(this.stage.camera.position);
        }
        s.snap = snap;
      }
    } else if (s.type === "rig" && s.group) {
      const it = s.item;
      if (this.tc.mode === "rotate" && (s.mates || []).length) {
        // a piece of a shape: the whole shape turns about its own middle
        const yaw = yawOf(this.proxy) - s.startYaw;
        const t = this._turnShape([{ it, g: s.group, g0: s.group0, riders: s.riders }, ...s.mates], yaw);
        const Y = new THREE.Vector3(0, 1, 0);
        for (const m of t.parts) {
          m.g.rotation.y = yaw;
          m.g.position.copy(m.g0).sub(t.mid).applyAxisAngle(Y, yaw).add(t.mid).add(t.shift);
          for (const r of m.riders) r.inst.holder.position.copy(r.pos).sub(t.mid).applyAxisAngle(Y, yaw).add(t.mid).add(t.shift);
        }
        this.handles.children.forEach((m, i) => m.position.copy(i === 0 ? t.ends[0].a : t.ends[0].b));
        s.yaw = yaw;
        s.shapeTurn = t;
      } else if (this.tc.mode === "rotate") {
        const yaw = yawOf(this.proxy) - s.startYaw;
        const turned = this._turnRig(it, yaw);
        const Y = new THREE.Vector3(0, 1, 0);
        // about its middle, then slid back inside the room the way the
        // engine will (so letting go doesn't jump)
        s.group.rotation.y = yaw;
        s.group.position.copy(s.group0).sub(turned.mid).applyAxisAngle(Y, yaw).add(turned.mid).add(turned.shift);
        for (const r of s.riders) r.inst.holder.position.copy(r.pos).sub(turned.mid).applyAxisAngle(Y, yaw).add(turned.mid).add(turned.shift);
        this.handles.children.forEach((m, i) => m.position.copy(i === 0 ? turned.a : turned.b));
        s.yaw = yaw;
        s.turned = turned;
      } else {
        const dd = this._rigDelta(it, d);
        s.group.position.copy(s.group0).add(dd);
        for (const r of s.riders) r.inst.holder.position.copy(r.pos).add(dd);
        for (const m of s.mates || []) {
          m.g.position.copy(m.g0).add(dd);
          for (const r of m.riders) r.inst.holder.position.copy(r.pos).add(dd);
        }
        this.handles.children.forEach((m, i) => m.position.set(...(i === 0 ? it.a : it.b)).add(dd));
        s.dd = dd;
      }
    } else if (s.type === "object" && s.group) {
      s.group.position.copy(s.group0).add(d);
      s.group.rotation.y = s.groupRot0 + (yawOf(this.proxy) - s.startYaw);
    } else if (s.type === "vertex") {
      const h = this.handles.children.find((m) => m.userData.handle === s.key);
      s.joint = s.parent.type === "rig" ? this._joint([this.proxy.position.clone()], s.parent.id) : null;
      this._showJoint(s.joint);
      if (h) h.position.copy(s.joint ? s.joint.point : this.proxy.position);
    } else if (s.type === "zone") {
      this.handles.children.forEach((m, i) => {
        const q = s.item.points[i];
        m.position.set(q[0] + d.x, m.position.y, q[1] + d.z);
      });
    } else if (s.type === "underlay" && this.stage.built.underlay) {
      const u = this.stage.built.underlay;
      u.position.set(this.proxy.position.x, 0.006, this.proxy.position.z);
      u.rotation.z = yawOf(this.proxy);
    }
    this.stage.dirty = true;
  }

  _end() {
    const s = this.sel;
    if (!s || !s.start) return;
    const p = this.proxy.position;
    const d = p.clone().sub(s.start);
    const r2 = (v) => Math.round(v * 100) / 100;
    const H = this.hooks;
    this.snapMark.visible = false;
    if (s.type === "light") {
      if (this.tc.mode === "rotate") {
        const e = new THREE.Euler().setFromQuaternion(this.proxy.quaternion, "YXZ");
        H.moveLight && H.moveLight(s.head, { rot: [Math.round(e.y * 180 / Math.PI), Math.round(-e.x * 180 / Math.PI)] });
      } else {
        const moves = [{ head: s.head, x: r2(p.x), y: r2(Math.max(0, p.y)), z: r2(p.z) }];
        for (const o of s.others) {
          const q = o.pos.clone().add(d);
          moves.push({ head: o.inst.head, x: r2(q.x), y: r2(Math.max(0, q.y)), z: r2(q.z) });
        }
        H.moveLights && H.moveLights(moves, s.snap ? { rig: s.snap.rig.id, t: s.snap.t } : null);
      }
    } else if (s.type === "rig") {
      const it = s.item;
      if (this.tc.mode === "rotate" && s.shapeTurn) {
        const e = s.shapeTurn.ends, v3 = (q) => [r2(q.x), r2(q.y), r2(q.z)];
        if (Math.abs(s.yaw || 0) > 1e-4) {
          H.updateItem && H.updateItem(s.id, { a: v3(e[0].a), b: v3(e[0].b),
            pieces: s.mates.map((m, i) => ({ id: m.id, a: v3(e[i + 1].a), b: v3(e[i + 1].b) })) });
        }
        s.shapeTurn = null;
      } else if (this.tc.mode === "rotate") {
        const t = s.turned || this._turnRig(it, yawOf(this.proxy) - s.startYaw);
        if (Math.abs(s.yaw || 0) > 1e-4) {
          H.updateItem && H.updateItem(s.id, { a: [r2(t.a.x), r2(t.a.y), r2(t.a.z)], b: [r2(t.b.x), r2(t.b.y), r2(t.b.z)] });
        }
        s.turned = null;
      } else {
        // the move rounded once, then added to both ends (each end rounded
        // on its own came out a few mm apart, and a shape's other pieces
        // didn't follow)
        const dd = s.dd || this._rigDelta(it, d);
        const m = [r2(dd.x), r2(dd.y), r2(dd.z)], r3 = (v) => Math.round(v * 1000) / 1000;
        const end = (e) => [r3(e[0] + m[0]), r3(Math.max(0, e[1] + m[1])), r3(e[2] + m[2])];
        H.updateItem && H.updateItem(s.id, { a: end(it.a), b: end(it.b) });
      }
    } else if (s.type === "object") {
      // its heading in -180..180 (the engine keeps -360..360)
      let rot = Math.round(-yawOf(this.proxy) * 180 / Math.PI);
      rot = ((rot + 540) % 360) - 180;
      H.updateItem && H.updateItem(s.id, { x: r2(p.x), y: r2(Math.max(0, p.y)), z: r2(p.z), rot });
    } else if (s.type === "vertex") {
      const base = s.parent;
      if (base.type === "rig") {
        const q = s.joint ? s.joint.point : p;
        const end = [r2(q.x), r2(Math.max(0, q.y)), r2(q.z)];
        H.updateItem && H.updateItem(base.id, s.key === "a" ? { a: end } : { b: end });
      } else {
        const pts = base.item.points.map((q) => [...q]);
        pts[s.key] = [r2(p.x), r2(p.z)];
        H.updateItem && H.updateItem(base.id, { points: pts });
      }
    } else if (s.type === "zone") {
      const pts = s.item.points.map((q) => [r2(q[0] + d.x), r2(q[1] + d.z)]);
      H.updateItem && H.updateItem(s.id, { points: pts });
    } else if (s.type === "stage") {
      const st = this.venue.stage;
      H.updateStage && H.updateStage({ x: r2(p.x), z: r2(p.z - st.depth / 2) });
    } else if (s.type === "underlay") {
      H.updateUnderlay && H.updateUnderlay({ x: r2(p.x), z: r2(p.z), rot: Math.round(-yawOf(this.proxy) * 180 / Math.PI) });
    }
    s.start = null;
  }

  // ------------------------------------------------------------ picking
  _ray(ev) {
    const dom = this.stage.renderer.domElement;
    const r = dom.getBoundingClientRect();
    const v = new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(v, this.stage.camera);
    return this.raycaster;
  }

  floorPoint(ev, y = 0) {
    const ray = this._ray(ev).ray;
    const hit = ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), -y), new THREE.Vector3());
    return hit;
  }

  /** What is under the pointer: a handle, a light, a venue item, or floor. */
  pick(ev) {
    const rc = this._ray(ev);
    const handle = rc.intersectObjects(this.handles.children, false)[0];
    if (handle) return { type: "handle", key: handle.object.userData.handle };
    const inst = this.stage._hit(ev);
    if (inst) return { type: "light", head: inst.head };
    const built = this.stage.built;
    if (!built) return null;
    const targets = [];
    built.group.traverse((o) => {
      if (!o.isMesh || !o.visible || o.userData.crowd || o.userData.performer) return;
      if (o.userData.venueId || o.userData.venueKind === "stage") targets.push(o);
    });
    const hits = rc.intersectObjects(targets, false);
    for (const h of hits) {
      const u = h.object.userData;
      if (u.venueKind === "underlay" && !(built.zones && built.zones.visible)) {
        return { type: "underlay" };
      }
      if (u.zone && !(built.zones && built.zones.visible)) continue;
      if (u.venueKind === "stage") return { type: "stage" };
      if (u.venueKind === "underlay") return { type: "underlay" };
      if (u.venueId) return { type: "item", id: u.venueId };
    }
    return null;
  }

  /** A click in arrange mode (not a drag, not on the gizmo). */
  click(ev) {
    if (this.draw) return this._drawClick(ev);
    if (this.tc.object && this.tc.axis) return;  // the click was on the gizmo
    const got = this.pick(ev);
    if (ev.shiftKey && got && got.type === "item") {
      const f = this._item(got.id);
      if (f && f.key !== "zones") return this.toggleMulti(got.id);
    }
    if (!got) { this.select(null); return; }
    if (got.type === "handle") return this.selectHandle(got.key);
    if (got.type === "light" && this.hooks.pickLight) this.hooks.pickLight(got.head, ev);
    this.select(got);
  }

  // ------------------------------------------------------------ drawing
  /** Start drawing: kind is "outline", "zone", "measure", or a drafted
   *  object - "door" (two clicks along a wall), "balcony" (two corners),
   *  "pillar" (each click places one, until Enter / Esc). */
  startDraw(kind, opts = {}) {
    this.cancelDraw();
    this.select(null);
    const mat = new THREE.LineBasicMaterial({ color: kind === "measure" ? 0x22d3ee : kind in TWO_CLICK || kind === "pillar" ? 0x60a5fa : 0xfbbf24,
      toneMapped: false, depthTest: false });
    const line = new THREE.Line(new THREE.BufferGeometry(), mat);
    line.renderOrder = 20;
    this.overlay.add(line);
    this.draw = { kind, points: [], line, opts, dots: new THREE.Group() };
    this.overlay.add(this.draw.dots);
    this._wireDrawMove();
    this.stage.dirty = true;
  }

  _wireDrawMove() {
    if (this._drawMove) return;
    this._drawMove = (ev) => {
      if (!this.draw) return;
      const p = this._snapPoint(ev);
      if (p) { this.draw.hover = p; this._updateLine(p); }
    };
    this._drawKey = (ev) => {
      const d = this.draw;
      if (!d) return;
      // a typed length: the next wall exactly this long, towards the pointer
      if (/^[0-9.,]$/.test(ev.key) && d.points.length && (d.kind === "outline" || d.kind === "zone")) {
        ev.preventDefault(); ev.stopPropagation();
        d.typed = (d.typed || "") + (ev.key === "," ? "." : ev.key);
        this._updateLine(d.hover);
        return;
      }
      if (ev.key === "Escape") {
        ev.stopPropagation();
        if (d.typed) { d.typed = ""; this._updateLine(d.hover); } else this.cancelDraw();
      }
      if (ev.key === "Enter") {
        ev.stopPropagation();
        const len = parseFloat(d.typed || "");
        if (d.typed && len > 0 && d.points.length) { d.typed = ""; this._placeTyped(len); } else this.finishDraw();
      }
      if (ev.key === "Backspace") {
        ev.preventDefault();
        if (d.typed) { d.typed = d.typed.slice(0, -1); this._updateLine(d.hover); return; }
        if (!d.points.length) return;
        d.points.pop();
        d.dots.remove(d.dots.children[d.dots.children.length - 1]);
        this._updateLine(d.hover);
      }
    };
    this.stage.renderer.domElement.addEventListener("pointermove", this._drawMove);
    window.addEventListener("keydown", this._drawKey, true);
  }

  _snapPoint(ev) {
    const p = this.floorPoint(ev, this.draw && this.draw.opts.y || 0);
    if (!p) return null;
    const grid = ev.altKey ? 0.01 : 0.1;
    p.x = Math.round(p.x / grid) * grid;
    p.z = Math.round(p.z / grid) * grid;
    const pts = this.draw ? this.draw.points : [];
    // Walls are drawn like a plan: square to the last corner unless Shift
    // is held (a zone or a measurement the other way round: free unless
    // Shift squares it).
    const walls = this.draw && this.draw.kind === "outline";
    if (pts.length && (walls ? !ev.shiftKey : ev.shiftKey)) {
      const last = pts[pts.length - 1];
      if (Math.abs(p.x - last.x) > Math.abs(p.z - last.z)) p.z = last.z; else p.x = last.x;
      // line up with the first corner, so the last wall closes square
      const first = pts[0];
      if (pts.length >= 2) {
        if (p.z === last.z && Math.abs(p.x - first.x) < 0.35) p.x = first.x;
        if (p.x === last.x && Math.abs(p.z - first.z) < 0.35) p.z = first.z;
      }
    }
    return p;
  }

  /** The next corner `len` m from the last one, towards the pointer. */
  _placeTyped(len) {
    const d = this.draw;
    const last = d.points[d.points.length - 1];
    const to = d.hover || { x: last.x + 1, z: last.z };
    let dx = to.x - last.x, dz = to.z - last.z;
    const n = Math.hypot(dx, dz) || 1;
    dx /= n; dz /= n;
    const p = new THREE.Vector3(Math.round((last.x + dx * len) * 1000) / 1000, 0, Math.round((last.z + dz * len) * 1000) / 1000);
    this._addPoint(p);
  }

  _addPoint(p) {
    const d = this.draw;
    d.points.push(p);
    const dot = new THREE.Mesh(this.handleGeo, this.handleMat);
    dot.scale.setScalar(0.6);
    dot.position.set(p.x, (d.opts.y || 0) + 0.03, p.z);
    d.dots.add(dot);
    this._updateLine(d.hover);
    if ((d.kind === "measure" || d.kind in TWO_CLICK) && d.points.length === 2) this.finishDraw();
  }

  _updateLine(hover) {
    const d = this.draw;
    const pts = d.points.map((q) => new THREE.Vector3(q.x, (d.opts.y || 0) + 0.03, q.z));
    if (hover) pts.push(new THREE.Vector3(hover.x, (d.opts.y || 0) + 0.03, hover.z));
    if (d.kind === "balcony" && pts.length === 2) {
      // the rectangle between the two corners
      const [a, b] = pts;
      pts.splice(1, 1, new THREE.Vector3(b.x, a.y, a.z), b, new THREE.Vector3(a.x, a.y, b.z), a.clone());
    } else if (d.kind === "pillar") {
      pts.length = 0;
    } else if (d.kind !== "measure" && !(d.kind in TWO_CLICK) && pts.length > 2) pts.push(pts[0].clone());
    d.line.geometry.dispose();
    d.line.geometry = new THREE.BufferGeometry().setFromPoints(pts);
    if (this.hooks.onDrawProgress) {
      const a = d.points[d.points.length - 1];
      const len = a && hover ? Math.hypot(hover.x - a.x, hover.z - a.z) : 0;
      this.hooks.onDrawProgress(d.kind, d.points.length, len, d.typed || "");
    }
    this.stage.dirty = true;
  }

  _drawClick(ev) {
    const d = this.draw;
    const p = this._snapPoint(ev);
    if (!p) return;
    if (d.kind === "pillar") {
      // one at a time: placed at once, and the tool stays on for the next
      this.hooks.onDrawn && this.hooks.onDrawn("pillar", [[Math.round(p.x * 100) / 100, Math.round(p.z * 100) / 100]], d.opts);
      return;
    }
    if (d.kind !== "measure" && !(d.kind in TWO_CLICK) && d.points.length >= 3) {
      const first = d.points[0];
      if (Math.hypot(p.x - first.x, p.z - first.z) < 0.3) return this.finishDraw();   // closed the shape
    }
    this._addPoint(p);
  }

  finishDraw() {
    const d = this.draw;
    if (!d) return;
    const pts = d.points.map((q) => [Math.round(q.x * 100) / 100, Math.round(q.z * 100) / 100]);
    const kind = d.kind, opts = d.opts;
    this.cancelDraw();
    if (kind === "measure" && pts.length === 2) {
      this.hooks.onMeasure && this.hooks.onMeasure(Math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]), pts);
    } else if (kind in TWO_CLICK) {
      if (pts.length === 2) this.hooks.onDrawn && this.hooks.onDrawn(kind, pts, opts);
    } else if (pts.length >= 3) {
      this.hooks.onDrawn && this.hooks.onDrawn(kind, pts, opts);
    }
  }

  cancelDraw() {
    const d = this.draw;
    if (!d) return;
    this.overlay.remove(d.line, d.dots);
    d.line.geometry.dispose();
    this.draw = null;
    if (this.hooks.onDrawProgress) this.hooks.onDrawProgress(null, 0, 0);
    this.stage.dirty = true;
  }

  get drawing() { return !!this.draw; }
}
