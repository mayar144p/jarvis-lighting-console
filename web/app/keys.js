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
import { toggleFull, getStage } from "./stagepanel.js";

// The cue the focused playback is on: the one O, I and D act on.
function currentCue() {
  const pb = ((state.snap && state.snap.playbacks) || []).find((p) => p.n === focusedPlayback());
  const c = pb && pb.index >= 0 ? (pb.stack || [])[pb.index] : null;
  return { pb: focusedPlayback(), cue: c ? c.n : null };
}

async function overwriteCue() {
  const { pb, cue } = currentCue();
  if (cue == null) return toast("No cue to overwrite - press GO or R first");
  run("record_cue", { playback: pb, cue }, { toast: true });
}

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
      if (document.body.classList.contains("stage-full")) { toggleFull(false); return; }
      return;
    }
    if (ctrl && e.key.toLowerCase() === "k") { e.preventDefault(); openCmdbar(); return; }
    if (ctrl && e.key.toLowerCase() === "s") { e.preventDefault(); saveShow((state.snap && state.snap.show_file) || ""); return; }
    if (typingInField(e) || anyModal() || cmdOpen()) return;
    if (ctrl && e.key.toLowerCase() === "z") { e.preventDefault(); run(e.shiftKey ? "redo" : "undo"); return; }
    if (ctrl && e.key.toLowerCase() === "y") { e.preventDefault(); run("redo"); return; }
    if (ctrl || e.altKey) return;
    const k = e.key;
    if (k === "/") { e.preventDefault(); openCmdbar(); return; }
    if (k === " " || k === "Enter") { e.preventDefault(); run("cue_go", { playback: focusedPlayback() }); return; }
    if (k === "?") { openHelp(); return; }
    const low = k.toLowerCase();
    if (low === "t") return e.shiftKey ? downbeat() : tap();
    if (low === "h") {
      const cur = (state.snap && state.snap.highlight) || {};
      return run("highlight", { state: !cur.on, solo: e.shiftKey });
    }
    if (low === "b") return run("cue_back", { playback: focusedPlayback() });
    if (low === "x") return run("blackout", { state: state.snap && state.snap.blackout ? 0 : 1 });
    if (low === "a") return e.shiftKey ? select([]) : run("select_all");
    if (low === "l") return run("locate");
    if (low === "c") return run("clear_programmer");
    if (low === "r") return openCueDialog(focusedPlayback());
    if (low === "o") return overwriteCue();
    if (low === "i") { const { pb, cue } = currentCue(); return run("insert_cue", { playback: pb, at: cue == null ? 1 : cue + 1 }); }
    if (low === "d") return deleteCue();
    if (low === "g") return makeGroup();
    if (low === "f") {
      const st = getStage();
      if (st) st.frame(selected().length ? selected() : null);
      return;
    }
    if (k === "ArrowUp" || k === "ArrowDown") {
      if (!selected().length) return;
      e.preventDefault();
      nudgeIntensity((k === "ArrowUp" ? 1 : -1) * (e.shiftKey ? 1 : 5));
      return;
    }
    if (/^[1-9]$/.test(k)) {
      const n = Number(k);
      if (patch().some((x) => x.head_no === n)) select([n], { add: e.shiftKey });
    }
  });
}
