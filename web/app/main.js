// Haze: boot the desk.
import { get, openStream } from "./api.js";
import { state, emit, setSnapshot, applySnapDiff, setLite, setLooks } from "./store.js";
import { $, toast } from "./ui.js";
import { initTopbar } from "./topbar.js";
import { initFixtures } from "./fixtures.js";
import { HAS_3D, stagePanel } from "./view3d.js";
import { initProgrammer } from "./programmer.js";
import { initPlaybacks } from "./playbacks.js";
import { initQuickButtons } from "./quickbuttons.js";
import { initWebMidi } from "./webmidi.js";
import { initControllers } from "./controllers.js";
import { initTempo } from "./tempo.js";
import { initStepFx } from "./stepfx.js";
import { initFxPanel } from "./fxpanel.js";
import { initMovePanel } from "./movepanel.js";
import { initTimeline } from "./timeline.js";
import { initCmdbar } from "./cmdbar.js";
import { initCopilot } from "./copilot.js";
import { initWorkspaces } from "./workspaces.js";
import { initErrorBadge } from "./errorbadge.js";
import { initKeys } from "./keys.js";
import { initWelcome } from "./welcome.js";
import { initConnections } from "./connections.js";
import { initTheme, applyTheme } from "./theme.js";
import { initHelp } from "./help.js";
import { initDeskBridge } from "./deskbridge.js";
import { initWinControls } from "./wincontrols.js";
import { upgradeGlyphs } from "./icons.js";

function wireGig() {
  // gig mode (big buttons and text for a laptop at a gig): saved per computer
  let gig = null;
  try { gig = localStorage.getItem("haze.gig"); } catch (e) { /* ignore */ }
  if (gig === "1") document.body.classList.add("gig");
  // a touch-only screen (no mouse) gets the big controls by itself, once,
  // saying so - the choice is kept either way (Settings -> Screen & 3D)
  else if (gig === null && matchMedia("(pointer: coarse) and (hover: none)").matches) {
    document.body.classList.add("gig");
    try { localStorage.setItem("haze.gig", "1"); } catch (e) { /* ignore */ }
    setTimeout(() => toast("Touch screen: Gig mode is on - big buttons and faders. Settings → Screen & 3D to change."), 2500);
  }
}

// Show mode: the 3D, the programmer and the playbacks are separate windows.
// A setting changed in one (Settings is in the programmer's) reaches the
// others at once - the 3D's quality, the look, Gig mode.
window.addEventListener("storage", (e) => {
  if (e.key === "haze.theme") applyTheme();
  else if (e.key === "haze.gig") document.body.classList.toggle("gig", e.newValue === "1");
  else if (e.key === "haze.quality" && HAS_3D) {
    stagePanel().then((m) => { const st = m && m.getStage(); if (st) st.setOptions({ quality: e.newValue || "high" }); }).catch(() => {});
  }
});

// The desktop app opens parts of the desk in their own windows (one per
// monitor): ?window=stage (the 3D, full), desk (fixtures + programmer),
// playbacks (playbacks + buttons, for a touch screen).  No window: all of it.
const WINDOW = new URLSearchParams(location.search).get("window") || "";
if (WINDOW) document.documentElement.dataset.window = WINDOW;

initTheme();                              // before anything draws: no bright flash in a dark venue
// The 3D starts at High (Max) quality.  A quality saved before that was the
// standard (Medium, the old default) goes back to High once; a choice made
// in Settings after this is kept.
try {
  if (localStorage.getItem("haze.qualityV") !== "2") {
    localStorage.setItem("haze.quality", "high");
    localStorage.setItem("haze.qualityV", "2");
  }
} catch (e) { /* private window: High anyway */ }
initDeskBridge();                         // the desktop app's questions show in the desk's own boxes
async function boot() {
  try {
    state.status = await get("/api/status");
  } catch (e) {
    state.status = null;
  }
  initTopbar();
  initWinControls();                        // the desktop app: no system title bar, the desk's own
  upgradeGlyphs();                          // ▾ ♪ ⛶ ... drawn as the desk's icons
  initFixtures();
  // the 3D view is off for now (view3d.js): the panels take the screen
  if (!HAS_3D) document.body.classList.add("no3d");
  else if (WINDOW !== "desk" && WINDOW !== "playbacks") {   // one 3D per computer is plenty
    // a computer whose graphics can't do 3D (no WebGL) still runs the desk
    try { (await stagePanel()).initStage(); } catch (e) {
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
  initControllers();
  initWebMidi();
  initTempo();
  initStepFx();
  initFxPanel();
  initMovePanel();
  initCmdbar();
  initCopilot();
  initKeys();
  initWorkspaces();
  initConnections();
  initErrorBadge();
  wireGig();
  initWelcome();
  initHelp();
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
