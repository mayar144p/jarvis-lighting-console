// Keyboard: a lighting desk is run from the keys.  Typing in a field never
// fires the rig.
import { tap, downbeat } from "./tempo.js";
import { state, patch, selected } from "./store.js";
import { run, select } from "./actions.js";
import { typingInField, anyModal, closeTopModal, confirmBox, toast } from "./ui.js";
import { openCmdbar, isOpen as cmdOpen, close as closeCmd } from "./cmdbar.js";
import { copilotOpen, closeCopilot } from "./copilot.js";
import { focusedPlayback } from "./playbacks.js";
import { openHelp, openCueDialog, saveShow } from "./dialogs.js";
import { makeGroup } from "./fixtures.js";
import { stagePanel } from "./view3d.js";

// The cue the focused playback is on: the one O, I and D act on.
function currentCue() {
  const pb = ((state.snap && state.snap.playbacks) || []).find((p) => p.n === focusedPlayback());
  const c = pb && pb.index >= 0 ? (pb.stack || [])[pb.index] : null;
  return { pb: focusedPlayback(), cue: c ? c.n : null };
}

// O = Update, as on a pro desk: the programmer merged INTO the cue that is
// on (what you didn't touch stays).  Shift+O replaces the whole cue with
// the programmer - after asking: it wipes everything else in it.
async function updateCue(replace = false) {
  const { pb, cue } = currentCue();
  if (cue == null) return toast("No cue to update - press GO or R first");
  if (replace && !await confirmBox("Replace the cue", `Cue ${cue} on PB${pb} becomes exactly what is in the programmer - everything else in it goes. Ctrl+Z brings it back.`, { ok: "Replace", danger: true })) return;
  run("record_cue", { playback: pb, cue, mode: replace ? "replace" : "merge" }, { toast: true });
}

// digits start a command line (12 THRU 16 @ 70, 3 GO), as on a pro desk -
// or, set in Settings -> Screen & 3D, pick heads 1-9 directly (the old way)
const digitsSelect = () => { try { return localStorage.getItem("jarvis.digitsSelect") === "1"; } catch (e) { return false; } };

async function deleteCue() {
  const { pb, cue } = currentCue();
  if (cue == null) return toast("No cue to delete - press GO first");
  if (await confirmBox("Delete cue", `Delete cue ${cue} on PB${pb}? Ctrl+Z brings it back.`, { ok: "Delete", danger: true })) {
    run("delete_cue", { playback: pb, cue });
  }
}

function nudgeIntensity(delta) {
  const vals = ((state.snap && state.snap.programmer) || {}).values || {};
  const first = selected()[0];
  const cur = first !== undefined && vals[first] && vals[first].dimmer !== undefined ? vals[first].dimmer : 0;
  run("set_intensity", { level: Math.max(0, Math.min(100, cur + delta)) });
}

export function initKeys() {
  document.addEventListener("keydown", (e) => {
    const ctrl = e.ctrlKey || e.metaKey;
    if (e.key === "Escape") {
      if (cmdOpen()) { closeCmd(); return; }
      if (closeTopModal()) return;
      if (copilotOpen()) { closeCopilot(); return; }
      if (document.body.classList.contains("stage-full")) { stagePanel().then((m) => m && m.toggleFull(false)); return; }
      return;
    }
    if (ctrl && e.key.toLowerCase() === "k") { e.preventDefault(); openCmdbar(); return; }
    if (ctrl && e.key.toLowerCase() === "s") { e.preventDefault(); saveShow((state.snap && state.snap.show_file) || ""); return; }
    // F1 / F2 / F3: Set up, Program, Live (as the mode buttons say)
    const fk = { F1: "setup", F2: "program", F3: "live" }[e.key];
    if (fk && !ctrl && !e.altKey && !anyModal()) {
      e.preventDefault();
      if (cmdOpen()) closeCmd();
      import("./workspaces.js").then((m) => m.useMode(fk));
      return;
    }
    if (typingInField(e) || anyModal() || cmdOpen()) return;
    if (ctrl && e.key.toLowerCase() === "z") { e.preventDefault(); run(e.shiftKey ? "redo" : "undo"); return; }
    if (ctrl && e.key.toLowerCase() === "y") { e.preventDefault(); run("redo"); return; }
    if (ctrl || e.altKey) return;
    const k = e.key;
    if (k === "/") { e.preventDefault(); openCmdbar(); return; }
    // GO is Space (and the GO keys) - never Enter: typed numbers then Enter
    // used to fire the next cue
    if (k === " ") { e.preventDefault(); run("cue_go", { playback: focusedPlayback() }); return; }
    if (k === "Enter") { e.preventDefault(); openCmdbar(); return; }
    if (k === "?") { openHelp(); return; }
    const low = k.toLowerCase();
    if (low === "t") return e.shiftKey ? downbeat() : tap();
    if (low === "h") {
      const cur = (state.snap && state.snap.highlight) || {};
      return run("highlight", { state: !cur.on, solo: e.shiftKey });
    }
    if (low === "b") return run("cue_back", { playback: focusedPlayback() });
    // [ and ]: the previous / next light (one at a time through the
    // selection, or through the whole rig from one light); { or }: all again
    if (k === "[" || k === "]") { e.preventDefault(); return run("select_step", { step: k === "]" ? "next" : "prev" }, { toast: true }); }
    if (k === "{" || k === "}") { e.preventDefault(); return run("select_step", { step: "all" }, { toast: true }); }
    // X blacks out at once; only Shift+X (or the BLACKOUT button) brings
    // the lights back - one stray key can't undo a blackout mid-show
    if (low === "x") {
      if (e.shiftKey) return run("blackout", { state: 0 });
      if (state.snap && state.snap.blackout) return toast("Blackout is on - Shift+X or the BLACKOUT button brings the lights back");
      return run("blackout", { state: 1 });
    }
    if (low === "a") return e.shiftKey ? select([]) : run("select_all");
    if (low === "l") return run("locate");
    if (low === "c") return run("clear_programmer");
    if (low === "r") return openCueDialog(focusedPlayback());
    if (low === "o") return updateCue(e.shiftKey);
    if (low === "i") { const { pb, cue } = currentCue(); return run("insert_cue", { playback: pb, at: cue == null ? 1 : cue + 1 }); }
    if (low === "d") return deleteCue();
    if (low === "g") return makeGroup();
    if (low === "f") {
      const sel = selected();
      stagePanel().then((m) => { const st = m && m.getStage(); if (st) st.frame(sel.length ? sel : null); });
      return;
    }
    if (k === "ArrowUp" || k === "ArrowDown") {
      if (!selected().length) return;
      e.preventDefault();
      nudgeIntensity((k === "ArrowUp" ? 1 : -1) * (e.shiftKey ? 1 : 5));
      return;
    }
    if (/^[0-9]$/.test(k)) {
      if (digitsSelect() && k !== "0") {
        const n = Number(k);
        if (patch().some((x) => x.head_no === n)) select([n], { add: e.shiftKey });
        return;
      }
      e.preventDefault();
      openCmdbar(k);
    }
  });
}
