// Tablets and phones at a gig: the screen stays awake while the console is
// open, and the console can be installed as a full-screen app.
//
// The wake lock needs a secure page (https, or this computer itself); on a
// plain http address from a tablet the browser has none, and we say so.

let sentinel = null;
let wanted = false;
let installPrompt = null;
let lastError = "";

export const wakeSupported = () => "wakeLock" in navigator && window.isSecureContext;
export const wakeOn = () => wanted;
export const wakeHeld = () => !!sentinel;
export const wakeError = () => lastError;

async function acquire() {
  if (!wanted || sentinel || !wakeSupported() || document.visibilityState !== "visible") return;
  try {
    sentinel = await navigator.wakeLock.request("screen");
    lastError = "";
    // the browser drops it when the tab is hidden; take it again on return
    sentinel.addEventListener("release", () => { sentinel = null; });
  } catch (e) {
    sentinel = null;
    lastError = e && e.message ? e.message : String(e);
  }
}

export async function setWake(on) {
  wanted = !!on;
  try { localStorage.setItem("jarvis.wake", wanted ? "1" : "0"); } catch (e) { /* ignore */ }
  if (wanted) await acquire();
  else if (sentinel) { try { await sentinel.release(); } catch (e) { /* ignore */ } sentinel = null; }
  return wakeHeld();
}

// Install as an app: Chrome / Edge / Android offer a prompt we keep for the
// Settings button; Safari has none (Share -> Add to Home Screen).
export const canInstall = () => !!installPrompt;
export const installed = () => window.matchMedia("(display-mode: fullscreen), (display-mode: standalone)").matches
  || navigator.standalone === true;

export async function install() {
  if (!installPrompt) return false;
  installPrompt.prompt();
  const r = await installPrompt.userChoice.catch(() => ({}));
  installPrompt = null;
  return r && r.outcome === "accepted";
}

export function initTablet() {
  let saved = null;
  try { saved = localStorage.getItem("jarvis.wake"); } catch (e) { /* ignore */ }
  // on by default: a console whose screen goes dark mid-show is a hazard
  wanted = saved !== "0";
  document.addEventListener("visibilitychange", acquire);
  // some browsers only grant it after a tap
  window.addEventListener("pointerdown", acquire, { passive: true });
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installPrompt = e; });
  window.addEventListener("appinstalled", () => { installPrompt = null; });
  acquire();
}
