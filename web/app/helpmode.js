// Help wherever you are:
//   * What's this?        click anything - its card from the guide
//   * Explain this screen the card for the screen / tab / window you're on
//   * Questions the guide couldn't answer  (kept on this computer)
//   * Hints when you look stuck - one line, a one-tap fix, once each
import { state, on } from "./store.js";
import { get, post } from "./api.js";
import { run } from "./actions.js";
import { h, toast, modal } from "./ui.js";
import { openGuide } from "./guidecard.js";

// -- What's this? -------------------------------------------------------------
let picking = false;

function nameOf(el) {
  // a button / tab by its name; else the panel it is in, by its heading
  const ctl = el.closest("button, [role=tab], summary, label, a, .tb-btn");
  const text = (x) => (x ? (x.getAttribute("aria-label") || x.textContent || x.title || "").replace(/\s+/g, " ").trim() : "");
  if (ctl && text(ctl) && text(ctl).length <= 50) return text(ctl);
  const panel = el.closest("[data-title], [aria-label], section, .panel, .modal, .dock, .prog");
  if (panel) {
    const head = panel.querySelector("h2, h3, h4, .panel-title, .modal-title");
    return panel.dataset.title || text(head) || panel.getAttribute("aria-label") || "";
  }
  return el.title || "";
}

export function whatsThis() {
  if (picking) return;
  picking = true;
  document.body.classList.add("whats-this");
  toast("What's this? Click anything to see what it is (Esc to stop)", "", 4000);
  const stop = () => {
    picking = false;
    document.body.classList.remove("whats-this");
    document.removeEventListener("click", pick, true);
    document.removeEventListener("keydown", esc, true);
  };
  const pick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    stop();
    const name = nameOf(e.target).replace(/[▶▾…✓]+/g, "").trim();
    if (!name) { toast("Nothing to explain there - try a button or a panel", "", 3000); return; }
    openGuide(`how do I use ${name}`);
  };
  const esc = (e) => { if (e.key === "Escape") { e.preventDefault(); stop(); } };
  setTimeout(() => {           // (not the click that started it)
    document.addEventListener("click", pick, true);
    document.addEventListener("keydown", esc, true);
  }, 0);
}

// -- Explain this screen: the window open, else the programmer tab, else the mode
const TAB_TOPIC = { FX: "Effects", Looks: "Palettes", SFX: "Lasers and special effects", Laser: "Lasers and special effects",
  Position: "Follow me speed aim", Rig: "Lining up a venue invert", Palettes: "Palettes", Colour: "colour palettes",
  Dimmer: "cues level", Beam: "gobos beam palettes" };
export function explainScreen() {
  const win = [...document.querySelectorAll(".modal[aria-label]")].pop();
  if (win) return openGuide(`how do I use ${win.getAttribute("aria-label")}`);
  const mode = document.body.dataset.mode;
  const tab = document.querySelector("#prog-tabs button[aria-selected='true']");
  if (mode !== "setup" && mode !== "live" && tab) {
    const t = tab.textContent.trim();
    return openGuide(`how do I use ${TAB_TOPIC[t] || t}`);
  }
  return openGuide(mode === "setup" ? "how do I set up the venue and the lights" : mode === "live" ? "how do I run the show live" : "how do I use the three modes");
}

// -- the questions the guide couldn't answer (on this computer only) ---------
export async function openUnanswered() {
  const d = await get("/api/howto?unanswered=1").catch(() => ({ unanswered: [] }));
  const rows = d.unanswered || [];
  const list = h("div.unanswered",
    rows.length ? h("table.mini-table", h("thead", h("tr", h("th", "Question"), h("th", "Asked"), h("th", "Why"))),
      h("tbody", ...rows.map((r) => h("tr", h("td", r.q), h("td.mono", `${r.n}×`), h("td.muted.small", r.why))))) :
      h("p.muted", "None yet - every question so far had an answer in the guide."));
  modal({
    title: "Questions the guide couldn't answer",
    body: h("div", h("p.muted.small", "Kept on this computer only, so the guide can be filled in where people get stuck. "
      + "\"Not what I asked\" on an answer adds it here too."), list),
    foot: rows.length ? [h("button.btn", { onclick: async () => { await post("/api/howto", { clear: true }).catch(() => {}); list.replaceChildren(h("p.muted", "Cleared.")); } }, "Clear the list")] : [],
  });
}

// -- hints when you look stuck: what's wrong, in one line, and the fix ---------
// Each says itself once (until the situation clears and comes back), and
// never while a window is open.
const said = new Set();
let hintBox = null;
let since = {};

const RULES = [
  { id: "blackout", when: (s) => s.blackout && (anyLevel(s) || anyPlaying(s)),
    text: "Blackout is on - nothing will light up until you release it.",
    fix: ["Release blackout", () => run("blackout", { state: 0 }, { toast: true })] },
  { id: "master", when: (s) => Number(s.master) === 0 && (anyLevel(s) || anyPlaying(s)),
    text: "The grand master is at 0 - every light is dark.",
    fix: ["Master to full", () => run("master", { level: 100 }, { toast: true })] },
  { id: "fader", when: (s) => (s.playbacks || []).some((p) => p.active && p.cue && Number(p.level) === 0),
    text: () => { const p = (state.snap.playbacks || []).find((x) => x.active && x.cue && Number(x.level) === 0); return `Playback ${p.n} is playing ${p.cue.name || "a cue"}, but its fader is down.`; },
    fix: ["Raise it", () => { const p = (state.snap.playbacks || []).find((x) => x.active && x.cue && Number(x.level) === 0); if (p) run("playback_level", { playback: p.n, level: 100 }, { toast: true }); }] },
  { id: "blind-live", when: (s) => s.dry_run && document.body.dataset.mode === "live" && (anyPlaying(s) || buttonsOn(s)),
    text: "You're playing the show, but the desk is BLIND - nothing reaches the real lights.",
    fix: ["Go live…", () => import("./topbar.js").then((m) => m.goLive())] },
  { id: "node", when: (s) => !s.dry_run && s.output && s.output.recent_error,
    text: "The desk can't send to the lights - the network or the node isn't answering.",
    fix: ["Output settings", () => import("./settings.js").then((m) => m.openSettingsAt("output"))] },
];
function anyLevel(s) {
  const vals = (s.programmer && s.programmer.values) || {};
  return Object.values(vals).some((row) => Number(row.dimmer || row.intensity || 0) > 0);
}
function buttonsOn(s) {
  const a = (s.quick && s.quick.active) || [];
  return Array.isArray(a) ? a.length > 0 : Object.keys(a).length > 0;
}
function anyPlaying(s) { return (s.playbacks || []).some((p) => p.active && Number(p.level) > 0); }

function check() {
  const s = state.snap;
  if (!s || document.querySelector(".modal")) return;
  const now = Date.now();
  for (const r of RULES) {
    let on = false;
    try { on = !!r.when(s); } catch (e) { on = false; }
    if (!on) { delete since[r.id]; said.delete(r.id); if (hintBox && hintBox.dataset.id === r.id) clearHint(); continue; }
    since[r.id] = since[r.id] || now;
    // a moment first: a blackout pressed on purpose isn't "stuck"
    if (now - since[r.id] < 6000 || said.has(r.id) || hintBox) continue;
    said.add(r.id);
    showHint(r);
    break;
  }
}

function clearHint() { if (hintBox) { hintBox.remove(); hintBox = null; } }

function showHint(r) {
  const text = typeof r.text === "function" ? r.text() : r.text;
  const [label, fn] = r.fix;
  hintBox = h("div.stuck-hint", { role: "status", dataset: { id: r.id } },
    h("span.stuck-i", { "aria-hidden": "true" }, "💡"),
    h("span.stuck-t", text),
    h("button.btn.small.primary", { type: "button", onclick: () => { clearHint(); fn(); } }, label),
    h("button.btn.small.ghost", { type: "button", "aria-label": "Not now", onclick: clearHint }, "✕"));
  document.body.append(hintBox);
  setTimeout(() => { if (hintBox && hintBox.dataset.id === r.id) clearHint(); }, 20000);
}

export function initHints() {
  on("snapshot", check);
  setInterval(check, 2000);
  // Shift+F1: What's this?
  document.addEventListener("keydown", (e) => { if (e.key === "F1" && e.shiftKey) { e.preventDefault(); whatsThis(); } });
}
export const _rules = RULES;          // (tests)
