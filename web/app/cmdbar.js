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
import { HAS_3D, stagePanel } from "./view3d.js";
import { openMacros, macroCandidates } from "./macros.js";
import { SETTINGS_PAGES, openSettingsAt } from "./settings.js";

const lazy = (mod, fn, ...args) => import(mod).then((m) => m[fn](...args));

// (a bare "go" / "back" is syntax; "go live", "back to blind" are actions)
const SYNTAX_START = /^\s*(\d|all\b|none\b|\*|group\b|cue\b|(?:go|back)\s*$|record\b|store\b|master\b|blackout\b|clear\b|palette\b|preset\b|align\b|distribute\b|mirror\b|home\b|select\b|fan\b|help\b|\?)/i;

const ACTIONS = [
  { t: "Go live… (send to the lights)", k: "go live output send lights on dmx start", run: () => import("./topbar.js").then((m) => m.goLive()) },
  { t: "Back to blind (stop sending)", k: "blind stop output sending back offline", run: () => run("set_output", { state: 0 }, { toast: true }) },
  { t: "Add lights…", k: "add fixtures patch fixture library", run: () => openAddDialog() },
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
  ...(HAS_3D ? [{ t: "Stage full screen", k: "full screen stage view", run: () => stagePanel().then((m) => m.toggleFull()) }] : []),
  { t: "Line up with the real lights", k: "line up align calibrate real lights invert pan tilt which way up offset", run: () => import("./lineup.js").then((m) => m.openLineUp()) },
  { t: "Teach the room by pointing", k: "zone dance floor bar mark out corners teach room point walls", run: () => import("./roomteach.js").then((m) => m.openRoomTeach()) },
  { t: "Not here - keep beams out", k: "keep out no go wall avoid never forbid not here mirror eyes", run: () => import("./roomteach.js").then((m) => m.openRoomTeach("keepout")) },
  { t: "Keep lights in a zone", k: "stay in zone confine dance floor only bar only", run: () => import("./roomteach.js").then((m) => m.openRoomTeach("stay")) },
  { t: "Plot", k: "plot plan front side rig drawing head numbers design", run: () => import("./plot.js").then((m) => m.openPlot()) },
  { t: "Ready? check", k: "ready check preflight doors gig before show", run: () => openReadyCheck() },
  { t: "Settings", k: "settings preferences options", run: () => openSettings() },
  // every Settings page, opened at that page
  ...SETTINGS_PAGES.map(([key, name, words]) => ({ t: `Settings: ${name}`, k: `settings ${name.toLowerCase()} ${words}`, run: () => openSettingsAt(key) })),
  // every Set up step
  { t: "Import a plot (MVR)…", k: "import mvr plot vectorworks capture", run: () => lazy("./dialogs.js", "importMvr") },
  { t: "Import a patch (CSV)…", k: "import csv patch spreadsheet", run: () => lazy("./dialogs.js", "openCsvImport") },
  { t: "DMX map…", k: "dmx map addresses clash channels universe", run: () => lazy("./fixtures.js", "openDmxMap") },
  { t: "Address them all", k: "auto address patch readdress clashes", run: () => run("auto_patch", {}, { toast: true }) },
  { t: "Arrange the room", k: "arrange room move lights place 3d", run: () => lazy("./venuepanel.js", "setArranging", true) },
  { t: "Make the room…", k: "room shape make room size walls draw floor plan", run: () => lazy("./roomdialog.js", "openRoomDialog") },
  { t: "Clear the venue to start again…", k: "reset venue clear room redesign start again empty", run: () => lazy("./venuepanel.js", "resetVenue") },
  { t: "Rigging…", k: "rigging truss bars pipes hang", run: () => lazy("./rigdialog.js", "openRigDialog") },
  { t: "Find nodes", k: "find nodes scan network art-net sacn search", run: () => lazy("./fixtures.js", "scanRig") },
  { t: "Ask the lights (RDM)…", k: "rdm ask lights discover address mode", run: () => lazy("./fixtures.js", "openRdm") },
  { t: "Songs…", k: "songs tracks rekordbox set list music", run: () => lazy("./songs.js", "openSongs") },
  { t: "Autopilot…", k: "autopilot auto run show by itself ai operator", run: () => lazy("./autopilot.js", "openAutopilot") },
  { t: "MIDI monitor…", k: "midi monitor controller see notes", run: () => lazy("./monitors.js", "openMidiMonitor") },
  { t: "Invert pan / tilt (Line up)…", k: "invert pan tilt flip upside down swap", run: () => lazy("./lineup.js", "openLineUp") },
  { t: "Venue check-in…", k: "check in venue arrive lights found", run: () => lazy("./checkin.js", "openCheckin") },
  { t: "Test a light…", k: "test light check channels lamp", run: () => {
    const sel = new Set((state.snap && state.snap.selected) || []);
    const hd = patch().find((x) => sel.has(x.head_no)) || patch()[0];
    return hd ? lazy("./lightdialogs.js", "openLightTest", hd) : toast("Add a light first");
  } },
  { t: "Show backups", k: "backup save copy usb cloud", run: () => openSettingsAt("backup") },
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
  // every word typed starts a word of the action's name or its keywords
  // ("midi mon", "keep out"); the name itself first
  const typed = q.split(/\s+/);
  const scored = [];
  for (const a of ACTIONS) {
    const title = a.t.toLowerCase();
    const vocab = `${title} ${a.k}`.split(/[\s:()/…,-]+/).filter(Boolean);
    if (title.includes(q)) scored.push([0, a]);
    else if (typed.every((w) => vocab.some((v) => v.startsWith(w)))) scored.push([1, a]);
  }
  scored.sort((x, y) => x[0] - y[0]);
  for (const [, a] of scored) out.push({ kind: "action", title: a.t, run: a.run });
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
  // focused at once: a number typed fast ("12") keeps every digit
  const focus = () => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); };
  focus();
  setTimeout(focus, 10);
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
