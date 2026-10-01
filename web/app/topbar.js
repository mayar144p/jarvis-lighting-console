// Top bar: the show, the output state, undo, lock; and the status bar.
import { state, on, outputState } from "./store.js";
import { post } from "./api.js";
import { run } from "./actions.js";
import { $, h, menu, confirmBox, promptBox, toast } from "./ui.js";
import { openShowMenu, openSettings, openHelp } from "./dialogs.js";

const LABELS = {
  blind: ["BLIND", "nothing leaves this computer"],
  armed: ["READY", "output stopped"],
  live: ["LIVE", ""],
};

async function goLive() {
  const s = state.snap || {};
  const host = (s.output && s.output.host) || "the network";
  const ok = await confirmBox("Go live",
    `Send DMX to ${host}?\n\nEvery fixture on the rig will do what the desk says, starting now. BLACKOUT and Stop are one click away.`,
    { ok: "Go live", danger: true });
  if (!ok) return;
  if (s.dry_run) {
    const r = await run("set_dry_run", { state: false, confirm: true });
    if (!r.ok) return;
  }
  const r = await run("set_output", { state: 1, confirm: true });
  if (r.ok) toast("LIVE - sending to " + host, "ok");
}

function outputMenu(anchor) {
  const st = outputState();
  menu(anchor, [
    st !== "live" ? { label: "Go live…", run: goLive } : null,
    st === "live" ? { label: "Stop sending", hint: "rig holds its last look", run: () => run("set_output", { state: 0 }) } : null,
    st !== "blind" ? { label: "Back to blind", hint: "safe", run: async () => {
      await run("set_dry_run", { state: true });
      toast("Blind - nothing leaves this computer");
    } } : null,
    "-",
    { label: "Blackout", hint: "X", run: () => run("blackout", { state: 1 }) },
  ]);
}

function renderOutput() {
  const s = state.snap;
  if (!s) return;
  const st = outputState();
  const box = $("#output");
  box.dataset.state = st;
  const [label, sub] = LABELS[st];
  $("#out-label").textContent = label;
  $("#out-sub").textContent = st === "live" ? `→ ${(s.output && s.output.host) || ""}` : sub;
  const arm = $("#out-arm");
  arm.textContent = st === "live" ? "Stop" : "Go live…";
  arm.title = st === "live" ? "Stop sending DMX (the rig holds its last look)" : "Start sending DMX to the rig";
}

function renderUndo() {
  const u = (state.snap && state.snap.undo) || {};
  const b = $("#undo-btn");
  b.disabled = !u.can_undo;
  $("#undo-label").textContent = u.can_undo ? "Undo " + String(u.undo || "").replace(/_/g, " ") : "Undo";
  b.title = u.can_undo ? `Undo ${String(u.undo || "").replace(/_/g, " ")} (Ctrl+Z)` : "Nothing to undo";
  $("#redo-btn").disabled = !u.can_redo;
  $("#redo-btn").title = u.can_redo ? `Redo ${String(u.redo || "").replace(/_/g, " ")} (Ctrl+Shift+Z)` : "Nothing to redo";
}

const LOCKS = {
  design: ["Design", "Design: edit anything"],
  operate: ["Operate", "Operate: the show runs, the show cannot be edited"],
  locked: ["Locked", "Locked: the show runs and the patch is frozen"],
};

function renderLock() {
  const s = state.snap;
  if (!s) return;
  const lk = s.lock || "design";
  const b = $("#lock-btn");
  b.dataset.state = lk;
  $("#lock-label").textContent = (LOCKS[lk] || LOCKS.design)[0];
  b.title = (LOCKS[lk] || LOCKS.design)[1];
}

function lockMenu(anchor) {
  const s = state.snap || {};
  const cur = s.lock || "design";
  const setTo = async (target) => {
    if (cur !== "design" && s.lock_has_password && target === "design") {
      const pw = await promptBox("Unlock", "Password", "", { ok: "Unlock" });
      if (pw === null) return;
      run("unlock", { password: pw }, { toast: true });
      return;
    }
    run("set_lock", { state: target }, { toast: true });
  };
  menu(anchor, [
    { label: "Design", hint: "edit anything", run: () => setTo("design") },
    { label: "Operate", hint: "run the show, no edits", run: () => setTo("operate") },
    { label: "Locked", hint: "patch frozen too", run: () => setTo("locked") },
    "-",
    { label: "Lock with a password…", run: async () => {
      const pw = await promptBox("Lock with a password", "Password (needed to unlock)", "", { ok: "Lock" });
      if (pw) run("set_lock", { state: "operate", password: pw }, { toast: true });
    } },
  ]);
}

function renderShow() {
  const s = state.snap;
  $("#show-name").textContent = (s && s.show_file) || "Untitled show";
}

// ------------------------------------------------------------ status bar
function renderStatus() {
  const s = state.snap;
  if (!s) return;
  const o = s.output || {};
  const st = outputState();
  const out = $("#st-out");
  // the error count is for the whole run; only a recent one is news
  const failing = o.errors && o.recent_error !== false;
  out.className = st === "live" ? (failing ? "bad" : "ok") : "warn";
  // plain words first; the numbers are one hover away
  out.textContent = st === "live"
    ? (failing ? `● LIVE - ${o.errors} send error(s)` + (o.last_error ? `: ${String(o.last_error).slice(0, 90)}` : "") : "● LIVE - the lights follow the console")
    : st === "blind" ? "○ BLIND - safe to program, nothing reaches the lights" : "○ Output stopped";
  out.title = st === "live" ? `${o.frames_sent || 0} frames sent · ${o.hz || 0} Hz`
    : st === "blind" ? `${o.simulated_frames || 0} frames simulated` : "";
  // frame timing: a steady beat, or the lights stutter
  const tm = $("#st-timing");
  const t = o.timing;
  tm.classList.toggle("hidden", !t);
  if (t) {
    tm.className = t.state === "steady" ? "ok" : t.state === "uneven" ? "warn" : "bad";
    tm.textContent = t.state === "steady" ? "● smooth" : t.state === "uneven" ? "● a few late frames" : "● frames stuttering";
    tm.title = `DMX timing, last ${Math.round(t.window * t.period_ms / 1000)} s: every ${t.period_ms} ms; `
      + `slowest gap ${t.worst_ms} ms, ${t.late} late frame(s)`
      + (t.state === "steady" ? "" : ". Close other screens or heavy programs on this computer.");
  }
  const net = $("#st-net");
  net.textContent = `to the DMX node at ${o.host || "?"}`;
  net.title = `${(o.transport || "artnet").toUpperCase()} → ${o.host || ""}`;
  $("#st-last").textContent = state.lastAction || "";
  const stale = s.stale_heads || [];
  if (stale.length) {
    $("#st-last").textContent = `⚠ cues point at ${stale.length} unpatched fixture(s): ${stale.slice(0, 6).join(", ")}`;
    $("#st-last").className = "st-grow warn";
  } else {
    $("#st-last").className = "st-grow";
  }
}

function renderLink() {
  const el = $("#st-feed");
  el.className = state.connected ? "ok" : "bad";
  el.textContent = state.connected ? "● connected" : "○ reconnecting…";
  const banner = $("#banner");
  if (!state.connected && state.linkError && state.everConnected) {
    banner.className = "banner";
    banner.textContent = "Lost contact with the Jarvis server - reconnecting. Nothing you do here reaches the rig until it is back.";
  } else if (banner.textContent.startsWith("Lost contact")) {
    banner.className = "banner hidden";
    banner.textContent = "";
  }
}

function syncBlind() {
  const b = (state.lite && state.lite.blind) || (state.snap && state.snap.blind) || {};
  const el = $("#blind-pill");
  if (!el) return;
  el.hidden = !b.on;
  el.textContent = b.cue ? `PREVIEW · cue ${b.cue}` : "PREVIEW";
}

function syncOperator() {
  const o = (state.lite && state.lite.ai_operator) || {};
  const el = $("#op-pill");
  if (!el) return;
  el.hidden = !o.on;
  el.textContent = o.on ? (o.busy ? "AI RUNNING · thinking…" : "AI RUNNING" + (o.last ? " · " + o.last : "")) : "";
}

export function initTopbar() {
  on("snapshot", syncBlind);
  on("lite", syncBlind);
  on("lite", syncOperator);
  $("#op-pill").addEventListener("click", (e) => menu(e.currentTarget, [
    { label: "I've got it", hint: "stop the AI - the lights stay as they are", run: async () => { await post("/api/console/assistant", { operator: "stop" }); toast("You have the lights", "ok"); } },
    { label: "What it's doing…", run: () => import("./aioperator.js").then((m) => m.openOperator()) },
  ]));
  $("#blind-pill").addEventListener("click", (e) => {
    const b = (state.snap && state.snap.blind) || {};
    menu(e.currentTarget, [
      b.cue ? { label: `Record into cue ${b.cue} (PB${b.playback})`, hint: "replace it with the programmer", run: () => run("record_cue", { playback: b.playback, cue: b.cue, mode: "replace" }, { toast: true }) } : null,
      { label: "Leave preview", hint: "drops what wasn't recorded", run: () => run("blind", { state: false }, { toast: true }) },
    ].filter(Boolean));
  });
  $("#out-arm").addEventListener("click", () => (outputState() === "live" ? run("set_output", { state: 0 }) : goLive()));
  $("#out-state").addEventListener("click", (e) => outputMenu(e.currentTarget));
  $("#out-state").style.cursor = "pointer";
  $("#undo-btn").addEventListener("click", () => run("undo", {}, { toast: true }));
  $("#redo-btn").addEventListener("click", () => run("redo", {}, { toast: true }));
  $("#lock-btn").addEventListener("click", (e) => lockMenu(e.currentTarget));
  $("#show-btn").addEventListener("click", (e) => openShowMenu(e.currentTarget, menu));
  $("#settings-btn").addEventListener("click", openSettings);
  $("#help-btn").addEventListener("click", openHelp);
  on("snapshot", () => { renderOutput(); renderUndo(); renderLock(); renderShow(); renderStatus(); });
  on("lite", () => { renderOutput(); renderUndo(); renderLock(); renderStatus(); });
  on("action", renderStatus);
  on("link", renderLink);
  void h;
}
