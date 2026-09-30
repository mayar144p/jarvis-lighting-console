// The command bar: one box for console syntax, quick actions, fixtures
// and plain English.
//
//   "1-4 red", "all dimmer 50", "cue 3 go"   -> the console's command line,
//                                               previewed as you type
//   "save", "add fixtures", "go live"        -> quick actions
//   "sharpy", "#12"                          -> select fixtures
//   anything else                            -> the copilot, plan first
import { post } from "./api.js";
import { state, patch } from "./store.js";
import { run, select } from "./actions.js";
import { $, h, toast } from "./ui.js";
import { openAddDialog, openHelp, openSettings, saveShow, openCueDialog, openReadyCheck } from "./dialogs.js";
import { askCopilot } from "./copilot.js";
import { toggleFull } from "./stagepanel.js";
import { openMacros, macroCandidates } from "./macros.js";

const SYNTAX_START = /^\s*(\d|all\b|none\b|\*|group\b|cue\b|go\b|back\b|record\b|store\b|master\b|blackout\b|clear\b|palette\b|preset\b|align\b|distribute\b|mirror\b|home\b|select\b|fan\b|help\b|\?)/i;

const ACTIONS = [
  { t: "Add fixtures…", k: "add patch fixture library", run: () => openAddDialog() },
  { t: "Save show", k: "save store file", run: () => saveShow((state.snap && state.snap.show_file) || "") },
  { t: "Record cue…", k: "record cue store", run: () => openCueDialog() },
  { t: "Select all", k: "select all everything", run: () => run("select_all") },
  { t: "Clear selection", k: "clear none deselect", run: () => select([]) },
  { t: "Clear programmer", k: "clear programmer reset", run: () => run("clear_programmer") },
  { t: "Locate", k: "locate home find", run: () => run("locate") },
  { t: "Blackout", k: "blackout dark kill panic", run: () => run("blackout", { state: 1 }) },
  { t: "Release blackout", k: "blackout off release lights back", run: () => run("blackout", { state: 0 }) },
  { t: "Stop all effects", k: "stop effects fx", run: () => run("stop_fx", {}) },
  { t: "Macros…", k: "macros macro script sequence", run: () => openMacros() },
  { t: "Undo", k: "undo oops", run: () => run("undo") },
  { t: "Stage full screen", k: "full screen stage view", run: () => toggleFull() },
  { t: "Ready? check", k: "ready check preflight doors gig before show", run: () => openReadyCheck() },
  { t: "Settings", k: "settings venue stage size midi network", run: () => openSettings() },
  { t: "Help", k: "help keys shortcuts", run: () => openHelp() },
];

let items = [];
let active = 0;
let previewTimer = 0;
let lastPreview = null;

function isSyntax(text) { return SYNTAX_START.test(text); }

function span(list) {
  const n = [...list].sort((a, b) => a - b);
  if (n.length > 2 && n[n.length - 1] - n[0] === n.length - 1) return `${n[0]}–${n[n.length - 1]}`;
  return n.join(", ");
}

function describe(step) {
  const p = step.params || {};
  switch (step.action) {
    case "select_heads": return `select ${p.heads ? span(p.heads) : p.head + (p.head_end ? "–" + p.head_end : "")}`;
    case "select_all": return "select all";
    case "select_group": return `select group ${p.group ?? p.n}`;
    case "set_colour": return `colour ${p.hex}`;
    case "set_intensity": return `intensity ${p.level}%`;
    case "set_attr_range": return p.clear ? `release ${p.attribute}` : `${p.attribute} ${p.value}${p.unit === "degree" ? "°" : ""}`;
    case "set_position": return `aim ${p.pan ?? "–"} / ${p.tilt ?? "–"}`;
    case "cue_go": return `GO${p.cue ? " cue " + p.cue : ""}`;
    default: return step.action.replace(/_/g, " ");
  }
}

function candidates(text) {
  const q = text.trim().toLowerCase();
  const out = [];
  if (!q) {
    return ACTIONS.slice(0, 8).map((a) => ({ kind: "action", title: a.t, run: a.run }));
  }
  for (const a of ACTIONS) {
    if (a.t.toLowerCase().includes(q) || a.k.split(" ").some((w) => w.startsWith(q))) {
      out.push({ kind: "action", title: a.t, run: a.run });
    }
  }
  out.push(...macroCandidates(q));
  for (const g of (state.snap && state.snap.groups) || []) {
    if (g.name.toLowerCase().includes(q)) out.push({ kind: "group", title: `Select ${g.name}`, desc: `${g.heads.length} fixtures`, run: () => select(g.heads) });
  }
  const words = q.replace(/^#/, "").split(/\s+/);
  const hits = patch().filter((hd) => {
    const hay = `${hd.head_no} ${hd.name} ${hd.manufacturer} ${hd.model} ${hd.body && hd.body.label}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
  if (hits.length && !/^\d+$/.test(q)) {
    out.push({ kind: "select", title: `Select ${hits.length === 1 ? hits[0].name : hits.length + " × " + (hits[0].body ? hits[0].body.label : "fixtures")}`, desc: hits.slice(0, 6).map((x) => "#" + x.head_no).join(" "), run: () => select(hits.map((x) => x.head_no)) });
  }
  for (const pb of (state.snap && state.snap.playbacks) || []) {
    if ((pb.name && pb.name.toLowerCase().includes(q)) || q === `pb${pb.n}`) out.push({ kind: "go", title: `GO on PB${pb.n} ${pb.name || ""}`, run: () => run("cue_go", { playback: pb.n }) });
  }
  return out.slice(0, 8);
}

function render(text) {
  const box = $("#cmd-results");
  const syntax = isSyntax(text);
  const mode = $("#cmd-mode");
  const ai = text.trim() && !syntax && !items.some((it) => it.kind !== "ai");
  mode.textContent = syntax ? "CMD" : text.trim() ? (ai ? "ASK" : "FIND") : "CMD";
  mode.classList.toggle("ai", mode.textContent === "ASK");
  const rows = items.map((it, i) => h("div.cmd-item" + (i === active ? ".on" : ""), {
    onmousemove: () => { if (active !== i) { active = i; render(text); } },
    onclick: () => execute(i),
  }, h("span.k", it.kind), h("span.t", it.title), it.desc ? h("span.d", it.desc) : null));
  box.replaceChildren(...rows);
  if (lastPreview) box.append(lastPreview);
}

function refresh() {
  const text = $("#cmd-input").value;
  const syntax = isSyntax(text);
  items = syntax ? [] : candidates(text);
  if (text.trim() && !syntax) {
    items.push({ kind: "ai", title: `Ask the copilot: “${text.trim()}”`, desc: "plan first, then you apply", run: () => ask(text) });
  }
  if (syntax && text.trim()) {
    items = [{ kind: "cmd", title: text.trim(), desc: "Enter to run", run: () => runSyntax(text) }];
  }
  active = 0;
  lastPreview = null;
  render(text);
  clearTimeout(previewTimer);
  if (syntax && text.trim()) {
    previewTimer = setTimeout(async () => {
      try {
        const d = await post("/api/console", { action: "run_command", params: { text, dry: true } });
        const r = d.result || {};
        if ($("#cmd-input").value !== text) return;
        const steps = (r.steps || []).map((s) => describe(s)).join("  →  ");
        lastPreview = h("div.cmd-out" + (r.ok ? "" : ".bad"), r.ok ? (steps || r.summary || "ok") : (r.error || "").split("\n")[0]);
        render(text);
      } catch (e) { /* preview only */ }
    }, 180);
  }
}

async function runSyntax(text) {
  const r = await run("run_command", { text }, { silentError: true });
  if (r.ok) { toast(r.summary || "Done", "ok"); close(); }
  else {
    lastPreview = h("div.cmd-out.bad", (r.error || "").split("\n")[0]);
    render(text);
  }
}

function ask(text) {
  close();
  askCopilot(text);
}

function execute(i) {
  const it = items[i];
  if (!it) return;
  if (it.kind !== "cmd" && it.kind !== "ai") close();
  it.run();
}

export function openCmdbar(prefill = "") {
  $("#cmdbar-scrim").classList.remove("hidden");
  const input = $("#cmd-input");
  input.value = prefill;
  refresh();
  setTimeout(() => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }, 10);
}

export function close() {
  $("#cmdbar-scrim").classList.add("hidden");
}

export const isOpen = () => !$("#cmdbar-scrim").classList.contains("hidden");

export function initCmdbar() {
  const input = $("#cmd-input");
  input.addEventListener("input", refresh);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { active = Math.min(items.length - 1, active + 1); render(input.value); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); render(input.value); e.preventDefault(); }
    else if (e.key === "Enter") { e.preventDefault(); execute(active); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
  });
  $("#cmdbar-scrim").addEventListener("mousedown", (e) => { if (e.target.id === "cmdbar-scrim") close(); });
  $("#palette-btn").addEventListener("click", () => openCmdbar());
}
