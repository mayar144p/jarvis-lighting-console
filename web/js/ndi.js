// NDI video from the network (OBS, Resolume, vMix, a camera...), in the
// desktop app: each source drawn into a canvas the LED screens in the 3D
// and the lights (the pixel mapper) take their picture from.  One feed per
// source, shared; the desktop app stops receiving when the last user lets go.
// In a browser there is no NDI (window.hazeDesk.ndi is the desktop app's).

const api = (typeof window !== "undefined" && window.hazeDesk && window.hazeDesk.ndi) || null;
export const ndiHere = !!api;

const feeds = new Map();     // source name -> {canvas, ctx, users, fns, frames, error}

if (api) {
  api.onFrame((f) => {
    const fd = f && feeds.get(f.name);
    if (!fd) return;
    if (f.error) {
      fd.error = f.error;
    } else {
      const w = f.w | 0, h = f.h | 0;
      const px = f.data instanceof Uint8Array ? f.data : new Uint8Array(f.data);
      if (w < 1 || h < 1 || px.byteLength < w * h * 4) return;
      if (fd.canvas.width !== w || fd.canvas.height !== h) { fd.canvas.width = w; fd.canvas.height = h; }
      fd.ctx.putImageData(new ImageData(new Uint8ClampedArray(px.buffer, px.byteOffset, w * h * 4), w, h), 0, 0);
      fd.error = "";
      fd.frames++;
      fd.at = performance.now();
    }
    for (const fn of fd.fns) { try { fn(fd); } catch (e) { /* one user's problem */ } }
  });
}

/** A source's picture: {canvas, onFrame(fn) -> stop, release(), frames, error}.
 *  Its canvas is black until the first frame comes. */
export function ndiFeed(name) {
  name = String(name || "");
  let fd = feeds.get(name);
  if (!fd) {
    const canvas = document.createElement("canvas");
    canvas.width = 16; canvas.height = 9;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, 16, 9);
    fd = { name, canvas, ctx, users: 0, fns: new Set(), frames: 0, at: 0, error: api ? "" : "NDI is in the desktop app" };
    feeds.set(name, fd);
    if (api) api.watch(name);
  }
  fd.users++;
  let done = false;
  const mine = new Set();
  return {
    canvas: fd.canvas,
    get frames() { return fd.frames; },
    get error() { return fd.error; },
    get live() { return fd.frames > 0 && performance.now() - fd.at < 2000; },
    onFrame(fn) { fd.fns.add(fn); mine.add(fn); return () => { fd.fns.delete(fn); mine.delete(fn); }; },
    release() {
      if (done) return;
      done = true;
      for (const fn of mine) fd.fns.delete(fn);
      if (--fd.users <= 0) {
        feeds.delete(name);
        if (api) api.unwatch(name);
      }
    },
  };
}

/** The NDI sources on the network now: [{name, url}] (none in a browser). */
export async function ndiSources() {
  if (!api) return [];
  try { return (await api.sources()) || []; } catch (e) { return []; }
}

/** Whether NDI works on this computer, and why not: {ok, why, version, out, inputs, settings}. */
export async function ndiStatus() {
  if (!api) return { ok: false, why: "NDI is in the desktop app" };
  try { return await api.status(); } catch (e) { return { ok: false, why: String(e.message || e) }; }
}

export const ndiSetOut = (cfg) => (api ? api.setOut(cfg) : Promise.resolve({ ok: false, why: "NDI is in the desktop app" }));

// ----------------------------------------------------------- the 3D out
// The desktop app says whether the 3D goes out and which window sends it;
// in that window the 3D view drawn last (the one on screen) sends.
let outState = { on: false, sending: false };
const outFns = new Set();
if (api) api.onOut((st) => { outState = st || { on: false, sending: false }; for (const fn of outFns) fn(outState); });
export const ndiOut = () => outState;
export function ndiOnOut(fn) { outFns.add(fn); return () => outFns.delete(fn); }

const views = [];            // the 3D views in this page that could send, newest first
/** A 3D view is on screen (true) or gone (false). */
export function ndiOffer(view, yes) {
  const i = views.indexOf(view);
  if (i >= 0) views.splice(i, 1);
  if (yes) views.unshift(view);
  if (api) api.canSend(views.length > 0);
}
export const ndiSender = () => views[0] || null;
export const ndiSendFrame = (w, h, data, fps) => { if (api) api.frame(w, h, data, fps); };
