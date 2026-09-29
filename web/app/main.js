// Jarvis: boot the desk.
import { get, openStream } from "./api.js";
import { state, emit, setSnapshot, setLite, setLooks } from "./store.js";
import { $, $$ } from "./ui.js";
import { initTopbar } from "./topbar.js";
import { initFixtures } from "./fixtures.js";
import { initStage } from "./stagepanel.js";
import { initProgrammer } from "./programmer.js";
import { initPlaybacks } from "./playbacks.js";
import { initQuickButtons } from "./quickbuttons.js";
import { initCmdbar } from "./cmdbar.js";
import { initCopilot } from "./copilot.js";
import { initKeys } from "./keys.js";

function wireMobile() {
  const app = $("#app");
  $$("#mobile-tabs button").forEach((b) => b.addEventListener("click", () => {
    app.dataset.view = b.dataset.mview;
    $$("#mobile-tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
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
  initPlaybacks();
  initQuickButtons();
  initCmdbar();
  initCopilot();
  initKeys();
  wireMobile();
  openStream({
    snapshot: setSnapshot,
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
