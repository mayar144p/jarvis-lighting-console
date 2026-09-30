// Jarvis: boot the desk.
import { get, openStream } from "./api.js";
import { state, emit, setSnapshot, applySnapDiff, setLite, setLooks } from "./store.js";
import { $, $$ } from "./ui.js";
import { initTopbar } from "./topbar.js";
import { initFixtures } from "./fixtures.js";
import { initStage } from "./stagepanel.js";
import { initProgrammer } from "./programmer.js";
import { initPlaybacks } from "./playbacks.js";
import { initQuickButtons } from "./quickbuttons.js";
import { initWebMidi } from "./webmidi.js";
import { initTablet } from "./tablet.js";
import { initFxPanel } from "./fxpanel.js";
import { initMovePanel } from "./movepanel.js";
import { initTimeline } from "./timeline.js";
import { initCmdbar } from "./cmdbar.js";
import { initCopilot } from "./copilot.js";
import { initKeys } from "./keys.js";

function wireMobile() {
  const app = $("#app");
  // gig mode (big buttons and text): saved per device; touch screens get it
  // from their own CSS rules anyway
  let gig = null;
  try { gig = localStorage.getItem("jarvis.gig"); } catch (e) { /* ignore */ }
  const touch = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  if (gig === "1" || (gig === null && touch)) document.body.classList.add("gig");
  $$("#mobile-tabs button[data-mview]").forEach((b) => b.addEventListener("click", () => {
    app.dataset.view = b.dataset.mview;
    $$("#mobile-tabs button[data-mview]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    const st = window.jarvisStage;
    if (st) setTimeout(() => st.resize(), 20);
  }));
}

async function boot() {
  try {
    state.status = await get("/api/status");
  } catch (e) {
    state.status = null;
  }
  initTopbar();
  initFixtures();
  initStage();
  initProgrammer();
  initTimeline();
  initPlaybacks();
  initQuickButtons();
  initWebMidi();
  initTablet();
  initFxPanel();
  initMovePanel();
  initCmdbar();
  initCopilot();
  initKeys();
  wireMobile();
  openStream({
    snapshot: setSnapshot,
    snapdiff: applySnapDiff,
    lite: setLite,
    look: setLooks,
  }, (connected, error) => {
    state.connected = connected;
    state.linkError = error;
    if (connected) state.everConnected = true;
    emit("link", { connected, error });
  });
}

boot();
