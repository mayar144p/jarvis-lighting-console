// The 3D view: off for now, while it is rebuilt.  Settings -> Screen & 3D
// turns it back on for this computer; a window of its own (Desk -> 3D view,
// Show mode: ?window=stage) or ?3d=1 always has it.  Off, three.js is
// never loaded and the panels share the whole screen.
import { toast } from "./ui.js";

const q = new URLSearchParams(location.search);

function saved() {
  try { return localStorage.getItem("haze.3d") === "on"; } catch (e) { return false; }
}

export const HAS_3D = q.get("window") === "stage" || q.get("3d") === "1" || (q.get("3d") !== "0" && saved());

/** Turn the 3D view on or off in this window: the desk reloads. */
export function set3d(on) {
  try { localStorage.setItem("haze.3d", on ? "on" : "off"); } catch (e) { /* private window */ }
  location.reload();
}

/** The 3D, when this window has it (null when it has not). */
export async function stagePanel() {
  return HAS_3D ? import("./stagepanel.js") : null;
}

/** For the tools that work in the 3D view: true when it is on; off, it
 *  says how to turn it on. */
export function needs3d(what) {
  if (HAS_3D) return true;
  toast(`${what} needs the 3D view: Settings → Screen & 3D`, "", 5000);
  return false;
}

/** Show the 3D visualiser: the desktop app's own 3D window (opened, or
 *  brought to the front); in a browser, the 3D page in a window of its own. */
export function open3d() {
  const w = window.hazeDesk && window.hazeDesk.win;
  if (w && w.pick) { w.pick("stage"); return; }
  const tab = window.open(`${location.pathname}?window=stage`, "haze-3d");
  if (tab) tab.focus();
}
