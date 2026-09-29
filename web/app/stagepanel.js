// The 3D stage in the middle of the desk, and its HUD.
import { Stage } from "/js/stage/stage.js";
import { get, modelBytes, token } from "./api.js";
import { initVenuePanel } from "./venuepanel.js";
import { state, on, patch } from "./store.js";
import { select } from "./actions.js";
import { $, $$ } from "./ui.js";

let stage = null;
let rigSig = "";
let modelSig = "";

export function getStage() { return stage; }

function fixturesFor(p) {
  return p.map((h) => ({
    head_no: h.head_no, name: h.name || "", manufacturer: h.manufacturer || "",
    model: h.model || "", mode: h.mode || "", kind: h.kind,
    x: +h.x || 0, y: +h.y || 0, z: +h.z || 0, body: h.body || null,
    stance: h.stance || null, mount: h.mount || null, rot: h.rot || null,
  }));
}

function syncRig() {
  if (!stage || !state.snap) return;
  const p = patch();
  const sig = JSON.stringify([p.map((h) => [h.head_no, h.model, h.mode, h.kind, h.x, h.y, h.z,
    h.body && h.body.type, h.stance, h.rot]), state.snap.venue]);
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
  $("#stage-hint").textContent = empty
    ? "The stage is empty - add fixtures and they appear here, modelled for their type and brand."
    : document.body.classList.contains("arranging")
      ? "Arrange: click a light, truss, object or zone to move it · W move · E rotate · Del delete · Esc done"
      : "Drag to orbit · right-drag to pan · scroll to zoom · click a light to select · Arrange to move things";
}

async function loadUnderlay(id) {
  const t = token();
  const r = await fetch("/api/console/underlay?id=" + encodeURIComponent(id),
    { headers: t ? { "X-Jarvis-Token": t } : {} });
  if (!r.ok) throw new Error("floor plan " + r.status);
  return r.blob();
}

export function initStage() {
  const el = $("#stage");
  stage = new Stage(el, {
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
  });
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
  toggle("#people-btn", "people", true);
  toggle("#zones-btn", "zones", false);
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
