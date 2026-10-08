// Offline programming with a video render (backlog A10 item 7): record the
// 3D view as a video for the client - while the timeline plays from the
// start, or for as long as you like while you run the show by hand.  The
// browser makes the file itself (MediaRecorder); nothing leaves the
// computer and the real lights needn't be connected.
import { $, h, modal, toast } from "./ui.js";
import { state } from "./store.js";
import { run } from "./actions.js";
import { pickFormat, SIZES, plannedSeconds, fileName, clock } from "./videoplan.js";

let rec = null;          // { recorder, chunks, stream, stage, t0, secs, timer, pill, fmt, mode }

export const recording = () => !!rec;

function timelineInfo() {
  const tl = (state.snap && state.snap.timeline) || {};
  const clips = (tl.tracks || []).reduce((n, t) => n + ((t.clips || []).length), 0);
  return { length: +tl.length || 0, clips };
}

/** The Record a video dialog. */
export function openVideoDialog(stage) {
  if (rec) { stopVideo(); return; }
  const fmt = typeof MediaRecorder !== "undefined" ? pickFormat((m) => MediaRecorder.isTypeSupported(m)) : null;
  if (!fmt || !stage || !stage.renderer || !stage.renderer.domElement.captureStream) {
    toast("This browser can't record video - use the desktop app or Chrome / Edge", "bad", 6000);
    return;
  }
  const tl = timelineInfo();
  const mode = h("select", { "aria-label": "What plays" },
    h("option", { value: "timeline", disabled: !tl.clips }, tl.clips ? `Play the timeline from the start (${clock(tl.length)})` : "Play the timeline (it has nothing on it yet)"),
    h("option", { value: "live" }, "Record what I do (run the show by hand)"));
  mode.value = tl.clips ? "timeline" : "live";
  const secs = h("input", { type: "number", min: 5, max: 600, value: 60, style: { width: "90px" }, "aria-label": "Seconds" });
  const size = h("select", { "aria-label": "Size" }, ...SIZES.map((s) => h("option", { value: s.id }, s.label)));
  const secsRow = h("label.field", h("span", "Stop after (seconds)"), secs);
  const sync = () => { secsRow.hidden = mode.value !== "live"; };
  mode.addEventListener("change", sync);
  sync();
  const close = modal({
    title: "Record a video",
    body: h("div",
      h("p.muted.small", { style: { marginTop: 0 } },
        "The 3D view becomes a video for the client. Pick the camera first (View ▾, or drag the view); "
        + "it records just the 3D picture, not the desk around it. The real lights needn't be connected."),
      h("label.field", h("span", "What plays"), mode),
      secsRow,
      h("label.field", h("span", "Size"), size),
      h("p.muted.small", `Saved as ${fmt.ext.toUpperCase()}. Stop early with the red button on the 3D view.`)),
    foot: [
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", { onclick: () => { close(); startVideo(stage, { mode: mode.value, secs: +secs.value, size: size.value, fmt }); } }, "● Record"),
    ],
  });
}

async function startVideo(stage, { mode, secs, size, fmt }) {
  const sz = SIZES.find((s) => s.id === size) || SIZES[0];
  const tl = timelineInfo();
  const total = plannedSeconds(mode, secs, tl.length);
  const stream = stage.startRecording(sz.long, 30);
  if (!stream) { toast("This browser can't record the 3D view", "bad"); return; }
  let recorder;
  try {
    recorder = new MediaRecorder(stream, { mimeType: fmt.mime, videoBitsPerSecond: sz.bits });
  } catch (e) {
    stage.stopRecording();
    toast("Couldn't start recording: " + e.message, "bad", 6000);
    return;
  }
  const chunks = [];
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  recorder.onstop = () => save(chunks, fmt);
  if (mode === "timeline") {
    await run("timeline_stop", {}, { silentError: true });
    await run("timeline_seek", { t: 0 }, { silentError: true });
  }
  const pill = h("button.rec-pill", { title: "Stop recording and save the video", onclick: () => stopVideo() },
    h("i"), h("b", "REC"), h("span", "0:00"), h("small", `/ ${clock(total)} · stop`));
  ($("#stage-wrap") || document.body).append(pill);
  recorder.start(1000);
  rec = { recorder, chunks, stream, stage, t0: performance.now(), secs: total, pill, fmt, mode };
  if (mode === "timeline") run("timeline_play", {}, { silentError: true });
  rec.timer = setInterval(() => {
    if (!rec) return;
    const el = (performance.now() - rec.t0) / 1000;
    pill.querySelector("span").textContent = clock(el);
    if (el >= rec.secs) stopVideo();
  }, 250);
  toast(mode === "timeline" ? "Recording: the timeline plays from the start" : `Recording for up to ${clock(total)} - run the show`, "ok");
}

/** Stop and save (the file downloads as the show's name and the time). */
export function stopVideo() {
  if (!rec) return;
  const r = rec;
  rec = null;
  clearInterval(r.timer);
  r.pill.remove();
  if (r.mode === "timeline") run("timeline_pause", {}, { silentError: true });
  try { r.recorder.stop(); } catch (e) { /* already stopped */ }
  r.stream && r.stream.getTracks().forEach((t) => t.stop());
  r.stage.stopRecording();
}

function save(chunks, fmt) {
  if (!chunks.length) {
    toast("Nothing was recorded - the 3D view was too slow to film at this size. Try Small, or Settings -> Screen & 3D -> Fast.", "bad", 9000);
    return;
  }
  const blob = new Blob(chunks, { type: fmt.mime.split(";")[0] });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = fileName(state.snap && state.snap.show_file, Date.now(), fmt.ext);
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  toast(`Video saved: ${a.download} (${(blob.size / 1e6).toFixed(1)} MB)`, "ok", 6000);
}
