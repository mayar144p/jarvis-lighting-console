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
import { initWelcome } from "./welcome.js";

function wireGig() {
  // gig mode (big buttons and text for a laptop at a gig): saved per computer
  let gig = null;
  try { gig = localStorage.getItem("jarvis.gig"); } catch (e) { /* ignore */ }
  if (gig === "1") document.body.classList.add("gig");
}

// The desktop app opens parts of the desk in their own windows (one per
// monitor): ?window=stage (the 3D, full), desk (fixtures + programmer),
// playbacks (playbacks + buttons, for a touch screen).  No window: all of it.
const WINDOW = new URLSearchParams(location.search).get("window") || "";
if (WINDOW) document.documentElement.dataset.window = WINDOW;

async function boot() {
  try {
    state.status = await get("/api/status");
  } catch (e) {
    state.status = null;
  }
  initTopbar();
  initFixtures();
  if (WINDOW !== "desk" && WINDOW !== "playbacks") {   // one 3D per computer is plenty
    // a computer whose graphics can't do 3D (no WebGL) still runs the desk
    try { initStage(); } catch (e) {
      console.error(e);
      const box = $("#stage-wrap");
      if (box) box.append(Object.assign(document.createElement("p"), { className: "stage-no3d muted",
        textContent: "This computer's graphics can't draw the 3D view. Everything else works - the lights, the programmer and the playbacks." }));
    }
  }
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
  initWelcome();
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
