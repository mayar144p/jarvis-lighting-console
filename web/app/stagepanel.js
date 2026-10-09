// The 3D stage in the middle of the desk, and its HUD.
import { Stage } from "/js/stage/stage.js";
import { setScreenMediaLoader } from "/js/stage/venue.js";
import { get, modelBytes, post, token } from "./api.js";
import { initVenuePanel } from "./venuepanel.js";
import { state, on, patch } from "./store.js";
import { run, select } from "./actions.js";
import { $, $$, menu, promptBox, toast } from "./ui.js";
import { atPointer, rowMenu } from "./fixtures.js";
import { openVideoDialog, stopVideo, recording } from "./videorec.js";

let stage = null;
let rigSig = "";
let modelSig = "";

export function getStage() { return stage; }

function fixturesFor(p) {
  return p.map((h) => ({
    head_no: h.head_no, name: h.name || "", manufacturer: h.manufacturer || "",
    model: h.model || "", mode: h.mode || "", kind: h.kind,
    x: +h.x || 0, y: +h.y || 0, z: +h.z || 0, body: h.body || null,
    stance: h.stance || null, mount: h.mount || null, rot: h.rot || null, gobos: h.gobos || null, gobos2: h.gobos2 || null,
    yaw: +h.yaw || 0,                      // which way its base faces (turns with its truss)
  }));
}

let syncLater = 0;
function syncRig() {
  if (!stage || !state.snap) return;
  // not in the middle of a drag in Arrange: a rebuild would put the piece
  // being dragged back where it was until the drag ends (it "snapped back")
  if (stage.editor && stage.editor.busy) {
    clearTimeout(syncLater);
    syncLater = setTimeout(syncRig, 120);
    return;
  }
  const p = patch();
  const sig = JSON.stringify([p.map((h) => [h.head_no, h.model, h.mode, h.kind, h.x, h.y, h.z,
    h.body && h.body.type, h.stance, h.rot, h.yaw]), state.snap.venue]);
  if (sig !== rigSig) {
    rigSig = sig;
    stage.setRig({ fixtures: fixturesFor(p), venue: state.snap.venue || {} });
    stage.setLooks(state.looks, 0);
  }
  const mSig = p.map((h) => h.manufacturer + "/" + h.model + "/" + h.mode).sort().join("|");
  if (mSig !== modelSig) {
    modelSig = mSig;
    get("/api/console/models").then((d) => {
      if (d && d.definitions) stage.setGdtf(d.definitions, modelBytes);
    }).catch(() => { /* procedural models stay; they are decoration */ });
  }
  stage.setSelected(state.snap.selected || []);
  const empty = !p.length;
  $("#stage-hint").classList.toggle("must", empty || document.body.classList.contains("arranging"));
  $("#stage-hint").textContent = empty
    ? "The stage is empty - add fixtures and they appear here, modelled for their type and brand."
    : document.body.classList.contains("arranging")
      ? "Arrange: click a light, truss, object or zone to move it · W move · E rotate · Del delete · Esc done"
      : "Drag to orbit · right-drag to pan · scroll to zoom · click a light to select · Shift-drag to box-select · Arrange to move things";
}

async function loadUnderlay(id) {
  const t = token();
  const r = await fetch("/api/console/underlay?id=" + encodeURIComponent(id),
    { headers: t ? { "X-Jarvis-Token": t } : {} });
  if (!r.ok) throw new Error("floor plan " + r.status);
  return r.blob();
}

async function loadGobo(ref) {
  const t = token();
  const r = await fetch("/api/console/gobo?ref=" + encodeURIComponent(ref),
    { headers: t ? { "X-Jarvis-Token": t } : {} });
  if (!r.ok) throw new Error("gobo " + r.status);
  return r.blob();
}

async function loadScreenMedia(id) {
  const t = token();
  const r = await fetch("/api/console/screen_media?id=" + encodeURIComponent(id),
    { headers: t ? { "X-Jarvis-Token": t } : {} });
  if (!r.ok) throw new Error("screen clip " + r.status);
  return r.blob();
}

export function initStage() {
  const el = $("#stage");
  stage = new Stage(el, {
    onMenu: (head, ev) => rowMenu(atPointer(ev), head),        // right-click a light
    onBox: (heads, mods) => {
      if (!heads.length) { if (!mods.add) select([]); return; }
      select(heads, { add: mods.add });
    },
    onPick: (head, mods) => {
      const cur = new Set((state.snap && state.snap.selected) || []);
      if (mods.toggle) {
        if (cur.has(head)) { cur.delete(head); select([...cur]); } else select([head], { add: true });
      } else if (mods.shift && cur.size) {
        const nums = patch().map((h) => h.head_no);
        const last = [...cur].pop();
        const a = nums.indexOf(last), b = nums.indexOf(head);
        select(nums.slice(Math.min(a, b), Math.max(a, b) + 1), { add: true });
      } else {
        select([head]);
      }
    },
    loadUnderlay,
    loadGobo,
  });
  setScreenMediaLoader(loadScreenMedia);
  window.jarvisStage = stage;          // for the browser console and tests
  initVenuePanel(stage);

  on("snapshot", syncRig);
  on("selection", (sel) => stage.setSelected(sel));
  on("lite", (l) => { if (l.selected) stage.setSelected(l.selected); });
  on("looks", (looks) => stage.setLooks(looks, 90));

  $$("#views button").forEach((b) => b.addEventListener("click", () => {
    stage.view(b.dataset.view);
    $$("#views button").forEach((x) => x.classList.toggle("on", x === b));
  }));
  $("#frame-btn").addEventListener("click", () => {
    const sel = (state.snap && state.snap.selected) || [];
    stage.frame(sel.length ? sel : null);
  });
  const haze = $("#haze");
  try { haze.value = localStorage.getItem("jarvis.haze") ?? haze.value; } catch (e) { /* ignore */ }
  stage.setOptions({ haze: +haze.value });
  haze.addEventListener("input", () => {
    stage.setOptions({ haze: +haze.value });
    try { localStorage.setItem("jarvis.haze", haze.value); } catch (e) { /* ignore */ }
  });
  const pref = (key, fallback) => { try { return localStorage.getItem("jarvis." + key) ?? fallback; } catch (e) { return fallback; } };
  const keep = (key, value) => { try { localStorage.setItem("jarvis." + key, value); } catch (e) { /* ignore */ } };
  const house = $("#house");
  house.value = pref("house", house.value);
  stage.setOptions({ house: +house.value, quality: pref("quality", "auto") });
  house.addEventListener("input", () => { stage.setOptions({ house: +house.value }); keep("house", house.value); });
  const toggle = (id, key, dflt) => {
    const btn = $(id);
    const on = pref(key, dflt ? "1" : "0") === "1";
    btn.classList.toggle("on", on);
    stage.setOptions({ [key]: on });
    btn.addEventListener("click", () => {
      const now = !btn.classList.contains("on");
      btn.classList.toggle("on", now);
      stage.setOptions({ [key]: now });
      keep(key, now ? "1" : "0");
    });
  };
  toggle("#zones-btn", "zones", false);
  toggle("#lightsonly-btn", "lightsOnly", false);
  // arranging the venue needs the venue: Lights only goes off
  $("#arrange-btn").addEventListener("click", () => {
    if ($("#lightsonly-btn").classList.contains("on")) $("#lightsonly-btn").click();
  });
  const dance = pref("dance", "1") === "1";
  // the crowd is off until asked for: it hid the rig (the choice is remembered)
  stage.setOptions({ people: pref("people", "0") === "1", dance, shadows: pref("shadows", "1") === "1" });
  // the mouse hint shows until the 3D view has been used once (empty and
  // arrange hints always show)
  try { if (localStorage.getItem("jarvis.stagehint")) document.body.classList.add("hint-seen"); } catch (e) { /* private */ }
  $("#stage").addEventListener("pointerdown", () => {
    document.body.classList.add("hint-seen");
    try { localStorage.setItem("jarvis.stagehint", "1"); } catch (e) { /* private */ }
  }, { once: true });
  $("#people-btn").classList.toggle("on", stage.options.people);
  $("#people-btn").addEventListener("click", (e) => crowdMenu(e.currentTarget, keep));
  $("#views-more").addEventListener("click", (e) => viewsMenu(e.currentTarget));
  $("#fullscreen-btn").addEventListener("click", toggleFull);
  on("lite", updateNowPlaying);
  on("snapshot", updateNowPlaying);
}

export function toggleFull(force) {
  const on = typeof force === "boolean" ? force : !document.body.classList.contains("stage-full");
  document.body.classList.toggle("stage-full", on);
  if (stage) setTimeout(() => stage.resize(), 30);
}

function updateNowPlaying() {
  const s = state.snap;
  const box = $("#now-playing");
  if (!s) return;
  const active = (s.playbacks || []).filter((p) => p.active && p.index >= 0);
  if (!active.length) { box.hidden = true; return; }
  box.hidden = false;
  box.replaceChildren(...active.slice(0, 3).map((p) => {
    const cue = (p.stack || [])[p.index];
    const span = document.createElement("span");
    span.innerHTML = `PB${p.n} <b>▶ ${cue ? (cue.name || "cue " + cue.n) : ""}</b>`;
    return span;
  }));
}

const DENSITY = [["Light", 0.2], ["Medium", 0.45], ["Packed", 0.8]];

function crowdMenu(btn, keep) {
  const v = (state.snap && state.snap.venue) || {};
  const c = v.crowd || { style: "varied", density: 0.45, show: true };
  const shown = stage.options.people;
  const tick = (on) => (on ? "✓ " : "   ");
  menu(btn, [
    { label: tick(shown) + "Show the crowd", run: () => {
      stage.setOptions({ people: !shown });
      btn.classList.toggle("on", !shown);
      keep("people", !shown ? "1" : "0");
    } },
    { label: tick(stage.options.dance) + "Dancing", run: () => {
      stage.setOptions({ dance: !stage.options.dance });
      keep("dance", stage.options.dance ? "1" : "0");
    } },
    { label: tick(stage.options.shadows) + "Shadows", hint: "The crowd, the stage and objects block the beams", run: () => {
      stage.setOptions({ shadows: !stage.options.shadows });
      keep("shadows", stage.options.shadows ? "1" : "0");
    } },
    "-",
    { label: tick(c.style === "simple") + "Simple figures", hint: "Plain grey, lightest to draw", run: () => run("venue_crowd", { style: "simple" }) },
    { label: tick(c.style !== "simple") + "Varied crowd", hint: "Clothes, heights, arms up", run: () => run("venue_crowd", { style: "varied" }) },
    "-",
    ...DENSITY.map(([label, d]) => ({
      label: tick(Math.abs((c.density ?? 0.45) - d) < 0.1) + label, run: () => run("venue_crowd", { density: d }),
    })),
  ]);
}

/** Each light model's real body from GDTF Share (the maker's 3D model,
 *  and its own gobo pictures); the profile and its channels stay exactly
 *  as they are. */
export async function realBodies() {
  toast("Looking the rig up on GDTF Share…", "", 4000);
  const d = await post("/api/gdtf/bodies", {}).catch((e) => ({ error: e.message }));
  if (d.error) {
    toast(d.code === "no_session" || d.code === "unauthorized"
      ? "Sign in to GDTF Share first: Add fixtures → GDTF Share (a free gdtf-share.com account)"
      : d.error, "bad", 7000);
    return;
  }
  const got = (d.done || []).filter((x) => x.ok), miss = (d.done || []).filter((x) => !x.ok);
  modelSig = "";                                   // load the new bodies
  syncRig();
  toast(!(d.done || []).length ? "Every light already uses its own GDTF file"
    : `${got.length} of ${d.done.length} models now look like the real light`
      + (miss.length ? ` · not available: ${miss.map((x) => `${x.model} (${x.reason})`).join(", ")}` : ""),
  got.length ? "ok" : "", 9000);
}

function viewsMenu(btn) {
  const v = (state.snap && state.snap.venue) || {};
  const sel = (state.snap && state.snap.selected) || [];
  const cams = v.cameras || [];
  menu(btn, [
    ...[["front", "Front", "1"], ["left", "House left", "2"], ["right", "House right", "3"], ["back", "Back", "4"],
      ["top", "Plan", "5"], ["overview", "Whole room (3D)", "6"]].map(([v, l, k]) => ({ label: l, hint: `key ${k}`, run: () => stage.view(v) })),
    "-",
    { label: "From the crowd", hint: "Eye level on the dance floor", run: () => stage.viewCrowd() },
    { label: "From the DJ / stage", hint: "Looking out at the room", run: () => stage.viewStage() },
    { label: "Through the selected light", hint: sel.length ? `Down #${sel[0]}'s beam` : "Select a light first",
      disabled: !sel.length, run: () => stage.lookThrough(sel[0]) },
    { label: stage.walking ? "Stop walking" : "Walk around", hint: "W A S D to move, drag to look, Esc stops", run: () => {
      if (stage.walking) { stage.walk(false); return; }
      stage.walk(true, () => toast("Stopped walking"));
      toast("Walking: W A S D (or arrows) to move, Shift to run, drag to look round, Esc to stop", "", 5000);
    } },
    { label: "Take a photo", hint: "the view at 4K, as a PNG", run: () => {
      const url = stage.photo(3840);
      const a = document.createElement("a");
      a.href = url;
      a.download = `jarvis-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.png`;
      a.click();
      toast("Photo saved", "ok");
    } },
    { label: recording() ? "■ Stop recording" : "Record a video…", hint: recording() ? "and save it" : "the 3D view for the client (MP4)",
      run: () => (recording() ? stopVideo() : openVideoDialog(stage)) },
    { label: "The makers' 3D bodies and gobos", hint: "from GDTF Share, for the 3D only", run: realBodies },
    "-",
    ...cams.map((c) => ({ label: "★ " + c.name, run: () => stage.setCamera({ pos: c.pos, target: c.target }) })),
    { label: "Save this view…", run: async () => {
      const name = await promptBox("Save view", "Name this view", "");
      if (!name) return;
      const cam = stage.cameraState();
      const r = await run("venue_camera", { name: name.trim(), pos: cam.pos.map((n) => +n.toFixed(2)), target: cam.target.map((n) => +n.toFixed(2)) });
      if (r.ok) toast(`Saved view "${name.trim()}"`, "ok");
    } },
    cams.length ? { label: "Delete a saved view…", run: async () => {
      const name = await promptBox("Delete view", `Which one? (${cams.map((c) => c.name).join(", ")})`, "");
      if (name) run("venue_camera", { name: name.trim(), remove: true }, { toast: true });
    } } : null,
  ]);
}
