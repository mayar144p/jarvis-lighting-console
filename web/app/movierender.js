// The best-quality film of the 3D: drawn frame by frame (backlog A18,
// step 7).
//
// Recording the 3D as it plays (videorec.js) films what the screen shows,
// so a slow computer films a slow, rough picture.  This makes the film the
// way a renderer does.  First the show plays once, at its own speed, while
// the desk notes every look the 3D is sent and where the camera is.  Then
// the 3D draws each picture of the film at full quality on a clock of its
// own (taking as long as each takes), and the browser's own encoder
// (WebCodecs) makes the video, with the timeline's music if it has some.
// Nothing leaves the computer.
import { Muxer as Mp4Muxer, ArrayBufferTarget as Mp4Target } from "/vendor/muxers/mp4-muxer.mjs";
import { Muxer as WebmMuxer, ArrayBufferTarget as WebmTarget } from "/vendor/muxers/webm-muxer.mjs";

export const FILM_SIZES = [
  { id: "hd", label: "Full HD (1920 × 1080)", w: 1920, h: 1080, bits: 16e6 },
  { id: "4k", label: "4K (3840 × 2160)", w: 3840, h: 2160, bits: 45e6 },
  { id: "small", label: "Small, for chat apps (1280 × 720)", w: 1280, h: 720, bits: 6e6 },
];
export const MAX_SECONDS = 600;

/** Can this browser make a film this way at all? */
export const canRender = () => typeof VideoEncoder !== "undefined" && typeof VideoFrame !== "undefined";

/** The video codec to use, best first: H.264 in an MP4 (plays everywhere),
 *  else VP9 or VP8 in a WebM.  null: none this browser can make. */
export async function pickCodec(w, h, fps, bits) {
  if (!canRender()) return null;
  const tries = [
    { codec: w * h > 2.2e6 ? "avc1.640033" : "avc1.640028", box: "mp4", mux: "avc", ext: "mp4", mime: "video/mp4" },
    { codec: "vp09.00.40.08", box: "webm", mux: "V_VP9", ext: "webm", mime: "video/webm" },
    { codec: "vp8", box: "webm", mux: "V_VP8", ext: "webm", mime: "video/webm" },
  ];
  for (const c of tries) {
    const cfg = { codec: c.codec, width: w, height: h, bitrate: bits, framerate: fps,
      ...(c.box === "mp4" ? { avc: { format: "avc" } } : {}) };
    try {
      const s = await VideoEncoder.isConfigSupported(cfg);
      if (s.supported) return { ...c, config: s.config || cfg };
    } catch (e) { /* not this one */ }
  }
  return null;
}

/** The audio codec for the film's box, or null (a film with no sound). */
export async function pickAudio(box, sampleRate, channels) {
  if (typeof AudioEncoder === "undefined") return null;
  const tries = box === "mp4" ? [{ codec: "mp4a.40.2", mux: "aac" }, { codec: "opus", mux: "opus" }] : [{ codec: "opus", mux: "A_OPUS" }];
  for (const a of tries) {
    const cfg = { codec: a.codec, sampleRate, numberOfChannels: channels, bitrate: 192000 };
    try {
      const s = await AudioEncoder.isConfigSupported(cfg);
      if (s.supported) return { ...a, config: s.config || cfg };
    } catch (e) { /* not this one */ }
  }
  return null;
}

/**
 * The first pass: note what the 3D is sent while the show plays - each
 * light's look when it changes (only the lights that changed, so a long
 * show stays small) and the camera when it moves.  stop() ends it.
 */
export class ShowNotes {
  constructor(stage) {
    this.stage = stage;
    this.t0 = performance.now();
    this.looks = [];                // {t, hold, ch: {head: row JSON}}
    this.cams = [];                 // {t, pos, target}
    this._last = new Map();
    this._cam = "";
    stage.noteLooks = (looks, hold) => this._look(looks, hold);
    this._raf = requestAnimationFrame(this._camera = this._camera.bind(this));
    this._look(stage.lastLooks || {}, 0);            // where it starts
  }

  _look(looks, hold) {
    const ch = {};
    let n = 0;
    for (const [head, row] of Object.entries(looks || {})) {
      const s = JSON.stringify(row);
      if (this._last.get(head) === s) continue;
      this._last.set(head, s);
      ch[head] = s;
      n++;
    }
    for (const head of [...this._last.keys()]) {
      if (!(head in (looks || {}))) { this._last.delete(head); ch[head] = "null"; n++; }
    }
    if (n || !this.looks.length) this.looks.push({ t: performance.now() - this.t0, hold, ch });
  }

  _camera() {
    if (this.done) return;
    const c = this.stage.cameraState();
    const key = c.pos.concat(c.target).map((v) => v.toFixed(3)).join(",");
    if (key !== this._cam) {
      this._cam = key;
      this.cams.push({ t: performance.now() - this.t0, pos: c.pos, target: c.target });
    }
    this._raf = requestAnimationFrame(this._camera);
  }

  /** How long the noted show is, in seconds. */
  get seconds() { return ((this.end || performance.now()) - this.t0) / 1000; }

  stop() {
    if (this.done) return;
    this.done = true;
    this.end = performance.now();
    cancelAnimationFrame(this._raf);
    if (this.stage.noteLooks) this.stage.noteLooks = null;
  }
}

/** Hand a screen's clip to the film's clock: paused, then at time t. */
async function seekClips(stage, t) {
  const list = (stage.built && stage.built.screens) || [];
  const waits = [];
  for (const m of list) {
    const v = m.material && m.material.userData && m.material.userData.video;
    if (!v || !(v.duration > 0)) continue;
    if (!v.paused) v.pause();
    const want = (t % v.duration);
    if (Math.abs(v.currentTime - want) < 1 / 120) continue;
    waits.push(new Promise((ok) => {
      const done = () => { v.removeEventListener("seeked", done); ok(); };
      v.addEventListener("seeked", done);
      setTimeout(done, 400);
      v.currentTime = want;
    }).then(() => { if (m.material.map) m.material.map.needsUpdate = true; }));
  }
  await Promise.all(waits);
}

/**
 * The second pass: draw the noted show frame by frame and encode it.
 *   size: FILM_SIZES entry; fps; codec (pickCodec); music: {buffer
 *   (AudioBuffer), offset (s: where it starts on the film)} or null;
 *   onProgress(done, total); cancelled() -> true to stop.
 * Returns {blob, ext, frames} (or {cancelled: true}).
 */
export async function renderFilm(stage, notes, { size, fps = 30, codec, music = null, onProgress = () => {}, cancelled = () => false }) {
  const W = size.w, H = size.h;
  const seconds = Math.min(MAX_SECONDS, notes.seconds);
  const frames = Math.max(1, Math.ceil(seconds * fps));
  // the sound: the timeline's music, from where it starts on the film
  let audio = null;
  if (music && music.buffer) {
    const ch = Math.min(2, music.buffer.numberOfChannels);
    const a = await pickAudio(codec.box, music.buffer.sampleRate, ch);
    if (a) audio = { ...a, ch, rate: music.buffer.sampleRate, next: 0 };
  }
  const target = codec.box === "mp4" ? new Mp4Target() : new WebmTarget();
  const Muxer = codec.box === "mp4" ? Mp4Muxer : WebmMuxer;
  const muxer = new Muxer({
    target,
    video: { codec: codec.mux, width: W, height: H, frameRate: fps },
    ...(audio ? { audio: { codec: audio.mux, numberOfChannels: audio.ch, sampleRate: audio.rate } } : {}),
    ...(codec.box === "mp4" ? { fastStart: "in-memory" } : {}),
    firstTimestampBehavior: "offset",
  });
  let failed = null;
  const venc = new VideoEncoder({ output: (chunk, meta) => muxer.addVideoChunk(chunk, meta), error: (e) => { failed = e; } });
  venc.configure(codec.config);
  let aenc = null;
  if (audio) {
    aenc = new AudioEncoder({ output: (chunk, meta) => muxer.addAudioChunk(chunk, meta), error: (e) => { failed = e; } });
    aenc.configure(audio.config);
  }
  // the music up to time t (s), in pieces of 1024 samples
  const soundTo = (t) => {
    if (!aenc) return;
    const buf = music.buffer, sr = audio.rate, n = 1024;
    while (audio.next < t) {
      const data = new Float32Array(n * audio.ch);
      const from = Math.round((audio.next - (music.offset || 0)) * sr);
      for (let c = 0; c < audio.ch; c++) {
        const src = buf.getChannelData(c);
        for (let i = 0; i < n; i++) {
          const k = from + i;
          data[c * n + i] = k >= 0 && k < src.length ? src[k] : 0;
        }
      }
      const ad = new AudioData({ format: "f32-planar", sampleRate: sr, numberOfFrames: n, numberOfChannels: audio.ch,
        timestamp: Math.round(audio.next * 1e6), data });
      aenc.encode(ad);
      ad.close();
      audio.next += n / sr;
    }
  };

  let flat = null;                                   // (WebGPU: the picture, flat, for the encoder)
  const full = {};                                   // the looks as noted so far
  let li = 0, ci = 0;
  const base = performance.now();
  stage.beginFilm(W, H);
  try {
    for (let i = 0; i < frames; i++) {
      if (cancelled()) return { cancelled: true };
      if (failed) throw failed;
      const t = i * 1000 / fps;
      // every look sent up to now, at the moment it was sent
      while (li < notes.looks.length && notes.looks[li].t <= t) {
        const ev = notes.looks[li++];
        for (const [head, s] of Object.entries(ev.ch)) {
          if (s === "null") delete full[head]; else full[head] = JSON.parse(s);
        }
        stage.filmLooks(base + ev.t, full, ev.hold);
      }
      while (ci + 1 < notes.cams.length && notes.cams[ci + 1].t <= t) ci++;
      if (notes.cams.length && (i === 0 || notes.cams[ci]._used !== true)) {
        stage.setCamera({ pos: notes.cams[ci].pos, target: notes.cams[ci].target });
        notes.cams[ci]._used = true;
      }
      await seekClips(stage, t / 1000);
      stage.filmFrame(base + t);
      const when = { timestamp: Math.round(t * 1000), duration: Math.round(1e6 / fps) };
      // WebGL 2: the canvas itself.  WebGPU: its canvas can't be read once
      // drawn, so the picture read back - by way of a 2D canvas: a frame
      // made straight from the pixels is marked as RGB colour, and its
      // MP4 then played back black
      const px = await stage.filmPixels();
      let src = stage.renderer.domElement;
      if (px) {
        const oc = flat || (flat = new OffscreenCanvas(px.width, px.height));
        let data = px.data;
        if (px.stride !== px.width * 4) {
          data = new Uint8Array(px.width * px.height * 4);
          for (let y = 0; y < px.height; y++) data.set(px.data.subarray(y * px.stride, y * px.stride + px.width * 4), y * px.width * 4);
        }
        oc.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(data.buffer, data.byteOffset, px.width * px.height * 4), px.width, px.height), 0, 0);
        src = oc;
      }
      const frame = new VideoFrame(src, when);
      venc.encode(frame, { keyFrame: i % Math.max(1, Math.round(fps * 2)) === 0 });
      frame.close();
      soundTo((i + 1) / fps);
      while (venc.encodeQueueSize > 3) await new Promise((ok) => setTimeout(ok, 4));
      onProgress(i + 1, frames);
      // let the page show the progress (and a Cancel click land)
      if (i % 2 === 0) await new Promise((ok) => setTimeout(ok, 0));
    }
    await venc.flush();
    if (aenc) await aenc.flush();
    if (failed) throw failed;
    muxer.finalize();
  } finally {
    try { venc.close(); } catch (e) { /* closed */ }
    try { if (aenc) aenc.close(); } catch (e) { /* closed */ }
    stage.endFilm();
  }
  return { blob: new Blob([target.buffer], { type: codec.mime }), ext: codec.ext, frames, sound: !!aenc };
}
