// Every button, key and command goes through `run`, so errors are reported
// the same way everywhere and the status bar always knows the last action.
import { act, noteError } from "./api.js";
import { state, emit } from "./store.js";
import { toast } from "./ui.js";

const LABELS = {
  set_intensity: "intensity", set_colour: "colour", set_position: "position",
  set_attribute: "attribute", select_heads: "select", clear_selection: "clear selection",
  select_all: "select all", cue_go: "GO", record_cue: "record cue",
};

export async function run(action, params = {}, opts = {}) {
  // heads of a multi-head light picked in the programmer (Heads: 1 3):
  // colour and attribute changes go to just those heads
  if ((action === "set_colour" || action === "set_attribute") && state.cells && state.cells.length
      && params.cell === undefined && !params.heads) {
    params = { ...params, cell: state.cells };
  }
  let res;
  try {
    res = await act(action, params);
  } catch (err) {
    toast(err.message || String(err), "bad");
    return { ok: false, error: err.message };
  }
  state.lastAction = (res.ok ? "" : "✕ ") + (res.summary || res.error || LABELS[action] || action);
  emit("action", { action, params, res });
  if (res.internal) noteError(`${action}: ${res.error}`);     // a bug in the desk, not "can't do that"
  if (!res.ok && !opts.silentError) toast(res.error || `${action} failed`, "bad");
  else if (res.ok && opts.toast) toast(typeof opts.toast === "string" ? opts.toast : (res.summary || "Done"), "ok");
  return res;
}

/** Selection changes feel instant: update locally, then tell the engine. */
export function select(heads, { add = false } = {}) {
  const list = [...new Set(heads.map(Number))].sort((a, b) => a - b);
  if (state.snap) {
    const next = add ? [...new Set([...(state.snap.selected || []), ...list])] : list;
    state.snap.selected = next.sort((a, b) => a - b);
    emit("selection", state.snap.selected);
  }
  if (!list.length && !add) return run("clear_selection", {}, { silentError: true });
  return run("select_heads", { heads: list, add }, { silentError: true });
}
