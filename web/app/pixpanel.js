// FX tab -> Paint the rig: a gradient of colours across the room, and a
// picture or a video laid over the lights.  Pixel bars and panels count
// cell by cell.  A picture is shrunk here (at most 64 pixels each way) and
// kept with the show; a video stays in this browser: while it plays here,
// its frames go to the desk and the lights follow.
import { post } from "./api.js";
import { run } from "./actions.js";
import { state } from "./store.js";
import { $, h, toast, confirmBox } from "./ui.js";

const SPACES = [["left-right", "Left → right"], ["right-left", "Right → left"], ["stage-out", "Stage → out"],
  ["back-in", "Out → stage"], ["up", "Bottom → top"], ["down", "Top → bottom"], ["centre-out", "Centre → out"],
  ["outside-in", "Outside → in"], ["around", "Round the room"]];
const SCROLL = [["0", "Still"], ["0.1", "Slow"], ["0.3", "Medium"], ["1", "Fast"], ["beat", "On the beat (a bar)"]];
const MAX_SIDE = 64;

const grad = { colours: ["#ff0040", "#2050ff"], space: "left-right", scroll: "0" };
const players = new Map();      // media id -> {video, url, timer, canvas}
let key = "";

const media = () => (state.snap && state.snap.media) || [];

/** w x h RGB of a drawable (an image or a video frame), shrunk to fit. */
function grab(src, sw, sh, canvas) {
  const k = Math.min(1, MAX_SIDE / Math.max(sw, sh));
  const w = Math.max(1, Math.round(sw * k)), h2 = Math.max(1, Math.round(sh * k));
  canvas.width = w; canvas.height = h2;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(src, 0, 0, w, h2);
  const px = ctx.getImageData(0, 0, w, h2).data;
  const rgb = new Uint8Array(w * h2 * 3);
  for (let i = 0, j = 0; i < px.length; i += 4, j += 3) { rgb[j] = px[i]; rgb[j + 1] = px[i + 1]; rgb[j + 2] = px[i + 2]; }
  let bin = "";
  for (let i = 0; i < rgb.length; i += 0x8000) bin += String.fromCharCode.apply(null, rgb.subarray(i, i + 0x8000));
  return { w, h: h2, data: btoa(bin) };
}

async function addFile(file) {
  if (!file) return;
  const name = file.name.replace(/\.[^.]+$/, "").slice(0, 40);
  if (file.type.startsWith("video/")) {
    const r = await run("media_save", { name, kind: "video" }, { toast: true });
    if (r.ok) startVideo(r.id, file);
    return;
  }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    await new Promise((ok, bad) => { img.onload = ok; img.onerror = () => bad(new Error("not a picture this browser can read")); img.src = url; });
    const g = grab(img, img.naturalWidth, img.naturalHeight, document.createElement("canvas"));
    await run("media_save", { name, kind: "image", ...g }, { toast: true });
  } catch (e) {
    toast(e.message || String(e), "bad");
  } finally {
    URL.revokeObjectURL(url);
  }
}

function startVideo(id, file) {
  stopVideo(id);
  const url = URL.createObjectURL(file);
  const video = h("video", { src: url, muted: true, loop: true, playsInline: true });
  video.muted = true;
  const canvas = document.createElement("canvas");
  const p = { video, url, canvas, timer: 0, busy: false };
  players.set(id, p);
  video.play().catch((e) => toast("The video won't play: " + (e.message || e), "bad"));
  p.timer = setInterval(async () => {
    if (p.busy || video.readyState < 2 || !video.videoWidth) return;
    p.busy = true;
    try { await post("/api/console/media_frame", { id, ...grab(video, video.videoWidth, video.videoHeight, canvas) }); } catch (e) { /* the next one */ }
    p.busy = false;
  }, 40);
  renderPix(true);
}

function stopVideo(id) {
  const p = players.get(id);
  if (!p) return;
  clearInterval(p.timer);
  p.video.pause();
  URL.revokeObjectURL(p.url);
  players.delete(id);
  renderPix(true);
}

function gradientBlock() {
  const swatches = grad.colours.map((c, i) => h("input.pix-col", { type: "color", value: c, title: `Colour ${i + 1}`,
    oninput: (e) => { grad.colours[i] = e.target.value; } }));
  const more = grad.colours.length < 6 ? h("button.chip", { title: "Another colour", onclick: () => { grad.colours.push("#ffffff"); renderPix(true); } }, "+") : null;
  const less = grad.colours.length > 2 ? h("button.chip", { title: "One colour fewer", onclick: () => { grad.colours.pop(); renderPix(true); } }, "−") : null;
  const space = h("select.select", { onchange: (e) => { grad.space = e.target.value; } }, ...SPACES.map(([v, l]) => h("option", { value: v }, l)));
  space.value = grad.space;
  const scroll = h("select.select", { onchange: (e) => { grad.scroll = e.target.value; } }, ...SCROLL.map(([v, l]) => h("option", { value: v }, l)));
  scroll.value = grad.scroll;
  const go = h("button.btn.small.primary", {
    title: "Paint the selected lights (pixel bars cell by cell) with these colours across the room",
    onclick: () => run("run_gradient", { colours: grad.colours, space: grad.space,
      ...(grad.scroll === "beat" ? { speed: 1, beats: 4 } : { speed: +grad.scroll }) }, { toast: true }),
  }, "Run gradient");
  return h("div.pix-row", h("span.k", "Gradient"), ...swatches, more, less, space, scroll, go);
}

function mediaRow(m) {
  const p = players.get(m.id);
  const runBtn = (view, label) => h("button.btn.small", {
    title: view === "front" ? "Lay it over the rig as seen from the front" : "Lay it over the rig as seen from above",
    onclick: () => run("run_media", { id: m.id, view }, { toast: true }) }, label);
  let play = null;
  if (m.kind === "video") {
    if (p) play = h("button.btn.small", { onclick: () => stopVideo(m.id) }, "■ Stop here");
    else {
      const pick = h("input", { type: "file", accept: "video/*", hidden: true,
        onchange: (e) => e.target.files[0] && startVideo(m.id, e.target.files[0]) });
      play = h("span", pick, h("button.btn.small", { title: "The video plays in this browser and the lights follow it: pick the file", onclick: () => pick.click() }, "▶ Play here…"));
    }
  }
  return h("div.fx-run",
    h("b", m.name), h("small", m.kind === "video" ? (m.playing ? "video · playing" : "video · not playing") : `picture · ${m.w}×${m.h}`),
    play, runBtn("front", "Front"), runBtn("top", "From above"),
    h("button.btn.small.ghost", { title: "Delete", onclick: async () => {
      if (await confirmBox("Delete", `Delete “${m.name}”?`, { ok: "Delete", danger: true })) { stopVideo(m.id); run("media_delete", { id: m.id }, { toast: true }); }
    } }, "×"));
}

export function renderPix(force = false) {
  const box = $("#pix-pane");
  if (!box) return;
  const k = JSON.stringify([media(), [...players.keys()], grad.colours.length]);
  if (!force && k === key) return;
  key = k;
  const file = h("input", { type: "file", accept: "image/*,video/*", hidden: true,
    onchange: (e) => { addFile(e.target.files[0]); e.target.value = ""; } });
  box.replaceChildren(
    gradientBlock(),
    h("div.pix-media", ...media().map(mediaRow)),
    h("div.row-btns", file, h("button.btn.small", { onclick: () => file.click(),
      title: "A picture is kept with the show; a video plays in this browser and the lights follow it" }, "+ Picture or video…")),
    h("p.muted.small", "Pixel bars and panels count cell by cell. Bring the lights up first: this paints colour, not brightness."));
}

export function stopAllVideos() { for (const id of [...players.keys()]) stopVideo(id); }
