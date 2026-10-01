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
import { initTempo } from "./tempo.js";
import { initStepFx } from "./stepfx.js";
import { initFxPanel } from "./fxpanel.js";
import { initMovePanel } from "./movepanel.js";
import { initTimeline } from "./timeline.js";
import { initCmdbar } from "./cmdbar.js";
import { initCopilot } from "./copilot.js";
import { initKeys } from "./keys.js";

function wireGig() {
  // gig mode (big buttons and text for a laptop at a gig): saved per computer
  let gig = null;
  try { gig = localStorage.getItem("jarvis.gig"); } catch (e) { /* ignore */ }
  if (gig === "1") document.body.classList.add("gig");
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
  initTempo();
  initStepFx();
  initFxPanel();
  initMovePanel();
  initCmdbar();
  initCopilot();
  initKeys();
  wireGig();
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
