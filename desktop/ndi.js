// NDI video in and out, for the desktop app.
//
// NDI is video over the network: OBS, Resolume, vMix, a camera or a media
// server on the same network sends it; the desk shows it on the 3D's LED
// screens and lays it over the lights (the pixel mapper).  And the 3D view
// goes out as NDI for a media server, an LED processor or a stream.
//
// Nothing of NDI ships with the desk: it uses the free NDI runtime the
// operator installs (NDI Tools, from ndi.video), called through koffi -
// nothing to build, and no NDI files of ours to keep up to date.
// NDI® is a registered trademark of Vizrt NDI AB.
"use strict";

const fs = require("fs");
const path = require("path");

const FRAME_VIDEO = 1;
const COLOR_RGBX_RGBA = 2;
const BANDWIDTH_LOWEST = 0, BANDWIDTH_HIGHEST = 100;
const FOURCC_RGBA = 0x41424752;              // 'R' 'G' 'B' 'A'
const FORMAT_PROGRESSIVE = 1;
const TIMECODE_SYNTH = 2n ** 63n - 1n;        // INT64_MAX: "the library times it"

let lib;                // undefined: not tried yet; null: no runtime here
let why = "";

/** Where the NDI runtime is on this computer, most likely first. */
function candidates(env = process.env, platform = process.platform) {
  const out = [];
  if (env.HAZE_NDI_LIB) out.push(env.HAZE_NDI_LIB);   // (the tests' stand-in, or a copy of the operator's)
  const dirs = [env.NDI_RUNTIME_DIR_V6, env.NDI_RUNTIME_DIR_V5].filter(Boolean);
  if (platform === "win32") {
    const name = process.arch === "ia32" ? "Processing.NDI.Lib.x86.dll" : "Processing.NDI.Lib.x64.dll";
    for (const d of dirs) out.push(path.join(d, name));
    const pf = env.ProgramFiles || "C:\\Program Files";
    out.push(path.join(pf, "NDI", "NDI 6 Runtime", "v6", name), path.join(pf, "NDI", "NDI 6 Tools", "Runtime", name),
      path.join(pf, "NDI", "NDI 5 Runtime", "v5", name), name);
  } else if (platform === "darwin") {
    for (const d of dirs) out.push(path.join(d, "libndi.dylib"));
    out.push("/usr/local/lib/libndi.dylib", "/Library/NDI SDK for Apple/lib/macOS/libndi.dylib", "libndi.dylib");
  } else {
    for (const d of dirs) out.push(path.join(d, "libndi.so.6"), path.join(d, "libndi.so.5"));
    out.push("/usr/lib/libndi.so.6", "/usr/local/lib/libndi.so.6", "libndi.so.6", "libndi.so.5", "libndi.so");
  }
  return out;
}

function load() {
  if (lib !== undefined) return lib;
  lib = null;
  let koffi;
  try { koffi = require("koffi"); } catch (e) { why = "the desk's helper for system calls (koffi) is missing"; return lib; }
  let handle = null, tried = [];
  for (const p of candidates()) {
    if (path.isAbsolute(p) && !fs.existsSync(p)) continue;
    try { handle = koffi.load(p); break; } catch (e) { tried.push(`${p}: ${e.message}`); }
  }
  if (!handle) { why = "NDI isn't installed on this computer"; return lib; }
  try {
    const Source = koffi.struct("HazeNdiSource", { p_ndi_name: "const char *", p_url_address: "const char *" });
    const FindCreate = koffi.struct("HazeNdiFindCreate", { show_local_sources: "bool", p_groups: "const char *", p_extra_ips: "const char *" });
    const RecvCreate = koffi.struct("HazeNdiRecvCreate", { source_to_connect_to: Source, color_format: "int", bandwidth: "int",
      allow_video_fields: "bool", p_ndi_recv_name: "const char *" });
    // (received: p_data is the library's memory, read then handed back)
    const Video = koffi.struct("HazeNdiVideo", { xres: "int", yres: "int", FourCC: "uint32", frame_rate_N: "int", frame_rate_D: "int",
      picture_aspect_ratio: "float", frame_format_type: "int", timecode: "int64", p_data: "void *",
      line_stride_in_bytes: "int", p_metadata: "const char *", timestamp: "int64" });
    // (sent: p_data is ours, a buffer that lives through the call)
    const VideoOut = koffi.struct("HazeNdiVideoOut", { xres: "int", yres: "int", FourCC: "uint32", frame_rate_N: "int", frame_rate_D: "int",
      picture_aspect_ratio: "float", frame_format_type: "int", timecode: "int64", p_data: "const uint8_t *",
      line_stride_in_bytes: "int", p_metadata: "const char *", timestamp: "int64" });
    const SendCreate = koffi.struct("HazeNdiSendCreate", { p_ndi_name: "const char *", p_groups: "const char *", clock_video: "bool", clock_audio: "bool" });
    const f = (sig) => handle.func(sig);
    lib = {
      koffi, Source, Video,
      initialize: f("bool NDIlib_initialize()"),
      version: f("const char *NDIlib_version()"),
      findCreate: f("void *NDIlib_find_create_v2(const HazeNdiFindCreate *p)"),
      findDestroy: f("void NDIlib_find_destroy(void *p)"),
      findSources: f("const HazeNdiSource *NDIlib_find_get_current_sources(void *p, _Out_ uint32_t *n)"),
      recvCreate: f("void *NDIlib_recv_create_v3(const HazeNdiRecvCreate *p)"),
      recvDestroy: f("void NDIlib_recv_destroy(void *p)"),
      recvCapture: f("int NDIlib_recv_capture_v2(void *p, _Out_ HazeNdiVideo *v, void *a, void *m, uint32_t timeout)"),
      recvFree: f("void NDIlib_recv_free_video_v2(void *p, const HazeNdiVideo *v)"),
      sendCreate: f("void *NDIlib_send_create(const HazeNdiSendCreate *p)"),
      sendDestroy: f("void NDIlib_send_destroy(void *p)"),
      sendVideo: f("void NDIlib_send_send_video_v2(void *p, const HazeNdiVideoOut *v)"),
      sendConnections: f("int NDIlib_send_get_no_connections(void *p, uint32_t timeout)"),
    };
    if (!lib.initialize()) { lib = null; why = "NDI won't start on this computer (its processor is too old for NDI)"; }
  } catch (e) {
    lib = null;
    why = `the NDI on this computer is too old or not the right kind (${e.message})`;
  }
  return lib;
}

// ------------------------------------------------------------------ sources
let finder = null;
function sources() {
  const L = load();
  if (!L) return [];
  if (!finder) finder = L.findCreate({ show_local_sources: true, p_groups: null, p_extra_ips: null });
  if (!finder) return [];
  const n = [0];
  const p = L.findSources(finder, n);
  if (!p || !n[0]) return [];
  return L.koffi.decode(p, L.koffi.array(L.Source, n[0])).map((s) => ({ name: s.p_ndi_name, url: s.p_url_address || "" }));
}

// ------------------------------------------------------------------ video in
// One receiver per source, shared by every screen and window showing it.
// Frames come at the lower NDI size (plenty for an LED wall in the 3D and
// for the lights), RGBA, the newest one only.
const inputs = new Map();    // name -> {recv, timer, users:Set(webContents), last, size}
function watch(name, wc, onFrame) {
  const L = load();
  if (!L) return { ok: false, why };
  let inp = inputs.get(name);
  if (!inp) {
    const recv = L.recvCreate({ source_to_connect_to: { p_ndi_name: name, p_url_address: null }, color_format: COLOR_RGBX_RGBA,
      bandwidth: process.env.HAZE_NDI_FULL ? BANDWIDTH_HIGHEST : BANDWIDTH_LOWEST, allow_video_fields: false, p_ndi_recv_name: "Haze" });
    if (!recv) return { ok: false, why: "NDI couldn't open that source" };
    inp = { recv, users: new Map(), last: 0, frames: 0 };
    inp.timer = setInterval(() => pull(L, name, inp), 1000 / 50);
    inputs.set(name, inp);
  }
  inp.users.set(wc, onFrame);
  return { ok: true };
}

function pull(L, name, inp) {
  let got = null;
  // (everything waiting: keep the newest, hand the rest straight back)
  for (let i = 0; i < 8; i++) {
    const v = {};
    const t = L.recvCapture(inp.recv, v, null, null, 0);
    if (t !== FRAME_VIDEO) break;
    if (got) L.recvFree(inp.recv, got.v);
    got = { v };
  }
  if (!got) return;
  const v = got.v;
  try {
    const w = v.xres, h = v.yres, stride = v.line_stride_in_bytes;
    if (w > 0 && h > 0 && stride >= w * 4 && v.p_data) {
      const src = Buffer.from(L.koffi.view(v.p_data, stride * h));
      let rgba = src;
      if (stride !== w * 4) {                // (rows padded: pack them)
        rgba = Buffer.alloc(w * h * 4);
        for (let y = 0; y < h; y++) src.copy(rgba, y * w * 4, y * stride, y * stride + w * 4);
      }
      inp.frames++;
      inp.size = [w, h];
      const frame = { name, w, h, data: rgba };
      for (const [wc, fn] of inp.users) {
        if (wc.isDestroyed && wc.isDestroyed()) { inp.users.delete(wc); continue; }
        fn(frame);
      }
    }
  } finally {
    L.recvFree(inp.recv, v);
  }
  if (!inp.users.size) stopInput(name);
}

function unwatch(name, wc) {
  const inp = inputs.get(name);
  if (!inp) return;
  inp.users.delete(wc);
  if (!inp.users.size) stopInput(name);
}

function unwatchAll(wc) {
  for (const name of [...inputs.keys()]) unwatch(name, wc);
}

function stopInput(name) {
  const inp = inputs.get(name);
  if (!inp) return;
  clearInterval(inp.timer);
  inputs.delete(name);
  try { load().recvDestroy(inp.recv); } catch { /* gone already */ }
}

// ----------------------------------------------------------------- video out
// The 3D view as one NDI source.  The window drawing it sends its frames;
// with nothing new (the 3D rests when nothing moves) the last one is sent
// again twice a second, so a receiver never sees the source go quiet.
let out = null;              // {send, name, last, timer, w, h, frames}
function outStart(name) {
  const L = load();
  if (!L) return { ok: false, why };
  name = String(name || "Haze 3D").slice(0, 60) || "Haze 3D";
  if (out && out.name === name) return { ok: true, name };
  outStop();
  const send = L.sendCreate({ p_ndi_name: name, p_groups: null, clock_video: false, clock_audio: false });
  if (!send) return { ok: false, why: "NDI couldn't make the output (is the name taken on this computer?)" };
  out = { send, name, last: null, at: 0, frames: 0 };
  out.timer = setInterval(() => {
    if (out && out.last && Date.now() - out.at > 500) sendNow(out.last);
  }, 500);
  return { ok: true, name };
}

function sendNow(f) {
  const L = load();
  if (!L || !out) return;
  L.sendVideo(out.send, { xres: f.w, yres: f.h, FourCC: FOURCC_RGBA, frame_rate_N: f.fps * 1000 || 30000, frame_rate_D: 1000,
    picture_aspect_ratio: f.w / f.h, frame_format_type: FORMAT_PROGRESSIVE, timecode: TIMECODE_SYNTH,
    p_data: f.data, line_stride_in_bytes: f.w * 4, p_metadata: null, timestamp: 0 });
  out.at = Date.now();
  out.frames++;
}

function outFrame(w, h, data, fps = 30) {
  if (!out) return false;
  w |= 0; h |= 0;
  if (w < 16 || h < 16 || w > 3840 || h > 2160 || !data || data.byteLength < w * h * 4) return false;
  const f = { w, h, fps, data: Buffer.from(data.buffer ? new Uint8Array(data.buffer, data.byteOffset, w * h * 4) : data) };
  out.last = f;
  sendNow(f);
  return true;
}

function outStop() {
  if (!out) return;
  clearInterval(out.timer);
  try { load().sendDestroy(out.send); } catch { /* gone already */ }
  out = null;
}

function status() {
  const L = load();
  let watching = 0;
  try { watching = out && L ? L.sendConnections(out.send, 0) : 0; } catch { watching = 0; }
  return {
    ok: !!L, why: L ? "" : why, version: L ? safe(() => L.version()) : "",
    out: out ? { name: out.name, frames: out.frames, watching, size: out.last ? [out.last.w, out.last.h] : null } : null,
    inputs: [...inputs.entries()].map(([name, i]) => ({ name, frames: i.frames, size: i.size || null })),
  };
}

const safe = (fn) => { try { return fn() || ""; } catch { return ""; } };

function shutdown() {
  for (const name of [...inputs.keys()]) stopInput(name);
  outStop();
  if (finder && lib) { try { lib.findDestroy(finder); } catch { /* not fatal */ } }
  finder = null;
}

module.exports = { candidates, load, sources, watch, unwatch, unwatchAll, outStart, outFrame, outStop, status, shutdown };
