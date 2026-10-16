// What the desk is connected to, in the status bar: DJ gear, controllers,
// OSC, Hue, a backup desk - each a chip that opens where it is set up.
// Only what is there (or switched on and not found) shows: nothing to read
// past when nothing is plugged in.
import { $, h } from "./ui.js";
import { state, on } from "./store.js";
import { connectedControllers } from "./controllers.js";
import { connectionChips } from "./rigmatch.js";

const open = (tab) => () => import("./settings.js").then((m) => m.openSettingsAt(tab));

function paint() {
  const el = $("#st-conn");
  if (!el || !state.snap) return;
  const chips = connectionChips(state.snap, connectedControllers());
  el.replaceChildren(...chips.map((c) => h("button.st-chip." + c.k, {
    type: "button", title: c.tip,
    onclick: c.tab === "songs" ? () => import("./songs.js").then((m) => m.openSongs()) : open(c.tab),
  }, c.text)));
}

export function initConnections() {
  on("snapshot", paint);
  setInterval(paint, 3000);              // controllers come and go without a snapshot
}
