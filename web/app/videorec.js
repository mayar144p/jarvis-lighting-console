// Offline programming with a video render (backlog A10 item 7): record the
// 3D view as a video for the client - while the timeline plays from the
// start, or for as long as you like while you run the show by hand.  The
// browser makes the file itself (MediaRecorder); nothing leaves the
// computer and the real lights needn't be connected.
import { $, h, modal, toast } from "./ui.js";
import { state } from "./store.js";
import { run } from "./actions.js";
import { pickFormat, SIZES, plannedSeconds, fileName, clock } from "./videoplan.js";
import { FILM_SIZES, MAX_SECONDS, ShowNotes, canRender, pickCodec, renderFilm } from "./movierender.js";

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
  const best = canRender();
  if ((!fmt && !best) || !stage || !stage.renderer || !stage.renderer.domElement.captureStream) {
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
  // as it plays (the screen, filmed), or the best: drawn frame by frame
  const quality = h("select", { "aria-label": "Quality" },
    h("option", { value: "live", disabled: !fmt }, "As it plays (films the screen)"),
    h("option", { value: "best", disabled: !best }, best ? "Best: drawn frame by frame (slower than the show)" : "Best (this browser can't)"));
  quality.value = fmt ? "live" : "best";
  const fps = h("select", { "aria-label": "Pictures a second" }, h("option", { value: "30" }, "30 a second"), h("option", { value: "60" }, "60 a second (smoother strobes)"));
  const filmSize = h("select", { "aria-label": "Size" }, ...FILM_SIZES.map((s) => h("option", { value: s.id }, s.label)));
  const liveRows = [h("label.field", h("span", "Size"), size)];
  const bestRows = [h("label.field", h("span", "Size"), filmSize), h("label.field", h("span", "Pictures"), fps),
    h("p.muted.small", "The show plays once while the desk notes every look and where the camera is; then the 3D draws "
      + "every picture at full quality (High, every shadow) as long as each takes, and makes the video. "
      + "Smooth on any computer - a slow one just takes longer. The timeline's music goes in.")];
  const saved = h("p.muted.small", "");
  const sync = () => {
    secsRow.hidden = mode.value !== "live";
    for (const r of liveRows) r.hidden = quality.value !== "live";
    for (const r of bestRows) r.hidden = quality.value !== "best";
    saved.textContent = quality.value === "best"
      ? "Saved as MP4 where this computer can make one, else WebM. Stop the first pass early with the button on the 3D view."
      : `Saved as ${fmt ? fmt.ext.toUpperCase() : "MP4"}. Stop early with the red button on the 3D view.`;
  };
  mode.addEventListener("change", sync);
  quality.addEventListener("change", sync);
  sync();
  const close = modal({
    title: "Record a video",
    body: h("div",
      h("p.muted.small", { style: { marginTop: 0 } },
        "The 3D view becomes a video for the client. Pick the camera first (View ▾, or drag the view); "
        + "it records just the 3D picture, not the desk around it. The real lights needn't be connected."),
      h("label.field", h("span", "What plays"), mode),
      secsRow,
      h("label.field", h("span", "Quality"), quality),
      ...liveRows, ...bestRows,
      saved),
    foot: [
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", { onclick: () => {
        close();
        if (quality.value === "best") startFilm(stage, { mode: mode.value, secs: +secs.value, size: filmSize.value, fps: +fps.value });
        else startVideo(stage, { mode: mode.value, secs: +secs.value, size: size.value, fmt });
      } }, "● Record"),
    ],
  });
}

async function startVideo(stage, { mode, secs, size, fmt: picked }) {
  let fmt = picked;
  const sz = SIZES.find((s) => s.id === size) || SIZES[0];
  const tl = timelineInfo();
  const total = plannedSeconds(mode, secs, tl.length);
  const stream = stage.startRecording(sz.long, 30);
  if (!stream) { toast("This browser can't record the 3D view", "bad"); return; }
  // playing the timeline: its music goes in the video too
  if (mode === "timeline") {
    const { timelineAudioStream } = await import("./timeline.js");
    const sound = timelineAudioStream();
    const av = sound ? pickFormat((m) => MediaRecorder.isTypeSupported(m), true) : null;
    if (sound && av && av.audio) {
      for (const t of sound.getAudioTracks()) stream.addTrack(t);
      fmt = av;
    }
  }
  let recorder;
  try {
    recorder = new MediaRecorder(stream, { mimeType: fmt.mime, videoBitsPerSecond: sz.bits,
      ...(fmt.audio ? { audioBitsPerSecond: 192000 } : {}) });
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

// -- the best quality: noted while it plays, then drawn frame by frame -----
async function startFilm(stage, { mode, secs, size, fps }) {
  const sz = FILM_SIZES.find((s) => s.id === size) || FILM_SIZES[0];
  const codec = await pickCodec(sz.w, sz.h, fps, sz.bits);
  if (!codec) { toast("This browser can't make a film frame by frame at this size - try a smaller one, or As it plays", "bad", 8000); return; }
  const tl = timelineInfo();
  const total = Math.min(MAX_SECONDS, plannedSeconds(mode, secs, tl.length));
  if (mode === "timeline") {
    await run("timeline_stop", {}, { silentError: true });
    await run("timeline_seek", { t: 0 }, { silentError: true });
  }
  const pill = h("button.rec-pill.noting", { title: "Stop here: the film is drawn from what was noted so far",
    onclick: () => stopVideo() }, h("i"), h("b", "NOTING"), h("span", "0:00"), h("small", `/ ${clock(total)} · then drawn`));
  ($("#stage-wrap") || document.body).append(pill);
  if (mode === "timeline") await run("timeline_play", {}, { silentError: true });
  const notes = new ShowNotes(stage);
  rec = { film: true, notes, stage, t0: performance.now(), secs: total, pill, mode, size: sz, fps, codec };
  rec.timer = setInterval(() => {
    if (!rec || !rec.film) return;
    const el = (performance.now() - rec.t0) / 1000;
    pill.querySelector("span").textContent = clock(el);
    if (el >= rec.secs) stopVideo();
  }, 250);
  toast(mode === "timeline" ? "The timeline plays once while the desk notes it; then the film is drawn" : `Run the show - noted for up to ${clock(total)}, then drawn`, "ok", 5000);
}

async function drawFilm(r) {
  const { notes, stage, size, fps, codec, mode } = r;
  let music = null;
  if (mode === "timeline") {
    const { timelineMusic } = await import("./timeline.js");
    music = timelineMusic();
  }
  let stop = false;
  const bar = h("i");
  const text = h("span", "Getting ready…");
  const box = h("div.film-progress", { role: "status", "aria-live": "polite" },
    h("b", "Drawing the film"), h("div.film-bar", bar), text,
    h("button.btn.small", { onclick: () => { stop = true; text.textContent = "Stopping…"; } }, "Cancel"));
  ($("#stage-wrap") || document.body).append(box);
  const t0 = performance.now();
  try {
    const out = await renderFilm(stage, notes, { size, fps, codec, music, cancelled: () => stop,
      onProgress: (done, total) => {
        bar.style.width = `${(100 * done / total).toFixed(1)}%`;
        const per = (performance.now() - t0) / done;
        text.textContent = `Picture ${done} of ${total} · about ${clock(Math.max(0, (total - done) * per / 1000))} left`;
      } });
    if (out.cancelled) { toast("Film stopped - nothing saved", "", 4000); return; }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(out.blob);
    a.download = fileName(state.snap && state.snap.show_file, Date.now(), out.ext);
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    toast(`Film saved: ${a.download} (${(out.blob.size / 1e6).toFixed(1)} MB, ${out.frames} pictures${out.sound ? ", with the music" : ""})`, "ok", 8000);
  } catch (e) {
    toast("The film couldn't be made: " + (e.message || String(e)), "bad", 9000);
  } finally {
    box.remove();
  }
}

/** Stop and save (the file downloads as the show's name and the time). */
export function stopVideo() {
  if (!rec) return;
  if (rec.film) {
    // the noting ends; the film is drawn from what was noted
    const r = rec;
    rec = null;
    clearInterval(r.timer);
    r.pill.remove();
    r.notes.stop();
    if (r.mode === "timeline") run("timeline_pause", {}, { silentError: true });
    if (r.notes.seconds < 0.5) { toast("Too short to film", "bad"); return; }
    drawFilm(r);
    return;
  }
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
