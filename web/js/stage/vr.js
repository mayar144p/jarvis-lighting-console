// Walk round the room in VR (WebXR).
//
// A headset on this computer (Quest with Link or Air Link, SteamVR, Windows
// Mixed Reality) with Chrome or Edge, or a headset's own browser: you stand
// on the dance floor at your own height and look round the room, the rig and
// every light as the desk sends them, live.  The left stick walks (the way
// you look), the right one turns you 30 degrees a flick; you stay inside the
// room.  Each eye gets the whole picture the screen does (stage.js,
// _presentEyes): the beams as light in the haze, the shadows, the mirror
// floor, the glow, the true colour, smooth edges, sharp to the edges of
// the lenses.  The 3D draws VR with WebGL 2, which gives the two eyes one
// picture side by side.
import * as THREE from "three";

/** Is there a VR headset this browser can use? */
export async function vrSupported() {
  try { return !!(navigator.xr && await navigator.xr.isSessionSupported("immersive-vr")); } catch (e) { return false; }
}

const DEG = Math.PI / 180;

export class VRWalk {
  constructor(stage) {
    this.stage = stage;
    this.session = null;
    this.rig = new THREE.Group();          // where you stand: the headset moves you from here
    this.rig.name = "vr-rig";
    this._turned = false;
    this._last = 0;
    this._fwd = new THREE.Vector3();
    this._eye = new THREE.Vector3();
  }

  get on() { return !!this.session; }

  /** Start: the headset's session (asked for from a click), you on the
   *  dance floor facing the stage.  An Error with `.webgl` set: the 3D is
   *  on WebGPU, and VR needs it on WebGL 2 (the caller can switch). */
  async start(onEnd = null) {
    const st = this.stage, r = st.renderer;
    if (!navigator.xr) throw new Error("This browser has no VR (WebXR)");
    // (WebGPU's VR gives each eye a picture of its own, which the eye by
    // eye drawing can't fill yet)
    if (r.backend && r.backend.isWebGPUBackend) { const err = new Error("VR needs the 3D on WebGL 2"); err.webgl = true; throw err; }
    const session = await navigator.xr.requestSession("immersive-vr", { optionalFeatures: ["local-floor", "bounded-floor"] });
    this.onEnd = onEnd;
    this.keep = { parent: st.camera.parent, pos: st.camera.position.clone(),
      quat: st.camera.quaternion.clone(), target: st.controls.target.clone(), controls: st.controls.enabled };
    r.xr.enabled = true;
    r.xr.setReferenceSpaceType("local-floor");
    // sharp to the edges of the lenses (a headset otherwise draws its
    // edges coarser to save time)
    r.xr.setFoveation(0);
    // the loop first: three.js takes the one set when the session starts and
    // runs it with the headset's pose (set after, the pose never came: you
    // stood with your eyes at the floor)
    r.setAnimationLoop((time, frame) => { if (this.session) this._frame(frame); });
    try {
      await r.xr.setSession(session);
    } catch (e) {
      r.xr.enabled = false;
      r.setAnimationLoop(null);
      try { await session.end(); } catch (e2) { /* gone */ }
      throw e;
    }
    this.session = session;
    // you: on the dance floor (or in front of the stage), facing it
    if (!st.camera.isPerspectiveCamera) st._perspective();
    const spot = st.standingSpot ? st.standingSpot() : { x: 0, z: 4 };
    this.rig.position.set(spot.x, 0, spot.z);
    // facing the stage, wherever it is (a headset starts looking along -z)
    const deck = st.built && st.built.venue && st.built.venue.stage;
    const dx = deck ? deck.x - spot.x : 0, dz = deck ? deck.z + deck.depth / 2 - spot.z : -1;
    this.rig.rotation.set(0, Math.hypot(dx, dz) > 0.2 ? Math.atan2(-dx, -dz) : 0, 0);
    st.scene.add(this.rig);
    this.rig.add(st.camera);
    st.camera.position.set(0, 0, 0);
    st.camera.quaternion.identity();
    st.controls.enabled = false;
    st.xrOn = true;
    st.dirty = true;
    this._last = performance.now();
    session.addEventListener("end", () => this._ended());
    return true;
  }

  /** Leave VR (the headset's own button does the same). */
  stop() {
    if (this.session) this.session.end().catch(() => this._ended());
  }

  _frame() {
    const now = performance.now();
    const dt = Math.min(0.1, (now - this._last) / 1000);
    this._last = now;
    this._move(dt);
    this.stage._tick(now, true);
  }

  /** The sticks: the left walks the way you look, the right turns you. */
  _move(dt) {
    const s = this.session;
    if (!s) return;
    const st = this.stage;
    let mx = 0, my = 0, turn = 0;
    for (const src of s.inputSources || []) {
      const gp = src.gamepad;
      if (!gp || !gp.axes || gp.axes.length < 4) continue;
      const x = gp.axes[2] || 0, y = gp.axes[3] || 0;
      if (src.handedness === "right") turn = x;
      else { mx = x; my = y; }
    }
    // a flick of the right stick: 30 degrees (smooth turning makes people sick)
    if (Math.abs(turn) > 0.6 && !this._turned) {
      this._turned = true;
      this.rig.rotation.y -= Math.sign(turn) * 30 * DEG;
    } else if (Math.abs(turn) < 0.3) this._turned = false;
    if (Math.hypot(mx, my) > 0.15) {
      // the way the headset looks, flat
      const cam = st.renderer.xr.getCamera();
      cam.getWorldDirection(this._fwd);
      this._fwd.y = 0;
      if (this._fwd.lengthSq() < 1e-6) this._fwd.set(0, 0, -1);
      this._fwd.normalize();
      const right = new THREE.Vector3(-this._fwd.z, 0, this._fwd.x);
      const speed = 1.6 * dt;
      this.rig.position.addScaledVector(this._fwd, -my * speed).addScaledVector(right, mx * speed);
      const R = st.room;
      this.rig.position.x = Math.max(R.x0 + 0.3, Math.min(R.x1 - 0.3, this.rig.position.x));
      this.rig.position.z = Math.max(R.z0 + 0.3, Math.min(R.z1 - 0.3, this.rig.position.z));
    }
  }

  _ended() {
    if (!this.session) return;
    const st = this.stage, r = st.renderer, k = this.keep;
    this.session = null;
    // (after three.js has had the session's end too: it puts the loop back)
    setTimeout(() => { if (!this.session) r.setAnimationLoop(null); }, 0);
    r.xr.enabled = false;
    this.rig.remove(st.camera);
    st.scene.remove(this.rig);
    if (k.parent) k.parent.add(st.camera);
    st.camera.position.copy(k.pos);
    st.camera.quaternion.copy(k.quat);
    st.controls.target.copy(k.target);
    st.controls.enabled = k.controls;
    st.controls.update();
    st.xrOn = false;
    st.resize();
    st.dirty = true;
    if (this.onEnd) this.onEnd();
  }
}
