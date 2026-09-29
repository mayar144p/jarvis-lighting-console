// The 3D stage in the middle of the desk, and its HUD.
import { Stage } from "/js/stage/stage.js";
import { get, modelBytes } from "./api.js";
import { state, on, patch } from "./store.js";
import { run, select } from "./actions.js";
import { $, $$, throttle } from "./ui.js";

let stage = null;
let rigSig = "";
let modelSig = "";

export function getStage() { return stage; }

function fixturesFor(p) {
  return p.map((h) => ({
    head_no: h.head_no, name: h.name || "", manufacturer: h.manufacturer || "",
    model: h.model || "", mode: h.mode || "", kind: h.kind,
    x: +h.x || 0, y: +h.y || 0, z: +h.z || 0, body: h.body || null,
  }));
}

function syncRig() {
  if (!stage || !state.snap) return;
  const p = patch();
  const sig = JSON.stringify([p.map((h) => [h.head_no, h.model, h.mode, h.kind, h.x, h.y, h.z, h.body && h.body.type]), state.snap.venue]);
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
    : "Drag to orbit · right-drag to pan · scroll to zoom · click a light to select · drag it to move (Shift = height)";
}

const moveLight = throttle((head, x, y, z) => {
  run("set_place", { head, x, y, z, kind: y >= 2 ? "truss" : "floor" }, { silentError: true });
}, 150);

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
    onMoveFixture: (head, x, y, z) => moveLight(head, x, y, z),
  });
  window.jarvisStage = stage;          // for the browser console and tests

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
  const people = $("#people-btn");
  people.addEventListener("click", () => {
    people.classList.toggle("on");
    stage.setOptions({ people: people.classList.contains("on") });
  });
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
