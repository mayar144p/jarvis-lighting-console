// How the desk's screen looks, per computer (backlog A10 item 10):
//   Show dark - the panels in dim red, so eyes stay used to a dark venue
//               (the 3D view keeps its true colours: it shows the lights);
//   accent    - the highlight colour;
//   panels    - their brightness (the 3D has its own House slider).
const KEY = "jarvis.theme";
export const ACCENTS = {
  violet: ["#8b8dff", "#7577f5"], blue: ["#4ea1ff", "#2f86ef"], cyan: ["#22d3ee", "#06b6d4"], green: ["#3dd68c", "#25b873"],
  amber: ["#f5a524", "#dd8f10"], red: ["#ff6b6b", "#ef4444"], pink: ["#f472b6", "#e2559c"], white: ["#e4e4ea", "#c9c9d2"],
};
export const DENSITIES = { compact: "Compact", standard: "Standard", roomy: "Roomy" };
export const SIZES = ["auto", 0.9, 1, 1.1, 1.25, 1.4];
const HEX = /^#[0-9a-f]{6}$/i;

/** Darker by a fraction (the accent's pressed / second shade). */
function shade(hex, k = 0.12) {
  const n = parseInt(hex.slice(1), 16);
  const c = (v) => Math.max(0, Math.round(v * (1 - k))).toString(16).padStart(2, "0");
  return `#${c(n >> 16)}${c((n >> 8) & 255)}${c(n & 255)}`;
}

export function themeNow() {
  let t = {};
  try { t = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { t = {}; }
  // Auto (the standard) sizes the desk to the screen; a size picked under
  // Size stays (a 100 % saved along with an accent, before Auto, was not picked)
  const picked = t.sizePicked === true;
  const scale = picked && t.scale !== "auto" && SIZES.includes(+t.scale) ? +t.scale : "auto";
  return { mode: t.mode === "showdark" ? "showdark" : "normal",
    accent: ACCENTS[t.accent] || HEX.test(String(t.accent)) ? t.accent : "violet",
    bright: Math.max(0.3, Math.min(1, +t.bright || 1)),
    density: DENSITIES[t.density] ? t.density : "standard", scale, sizePicked: picked,
    motion: t.motion === "reduced" ? "reduced" : "full" };
}

export function applyTheme(t = themeNow()) {
  const root = document.documentElement;
  root.dataset.theme = t.mode;
  root.dataset.density = t.density;
  root.dataset.motion = t.motion;
  setZoom(t.scale);
  const [a, a2] = ACCENTS[t.accent] || (HEX.test(String(t.accent)) ? [t.accent, shade(t.accent)] : ACCENTS.violet);
  root.style.setProperty("--accent", a);
  root.style.setProperty("--accent-2", a2);
  root.style.setProperty("--accent-bg", a + "24");
  const b = t.mode === "showdark" ? Math.min(t.bright, 0.75) : t.bright;
  root.style.setProperty("--panel-filter", t.mode === "showdark"
    ? `brightness(${b}) sepia(1) saturate(6) hue-rotate(-48deg)`
    : b < 0.999 ? `brightness(${b})` : "none");
  window.dispatchEvent(new Event("resize"));            // the 3D and the panels measure again
}

/** The biggest size this window has room for: the desk is laid out for at
 *  least 1280 px, so a 1440 px screen goes to 110%, a 1920 px one to 140%. */
export function maxScale() {
  return Math.max(1, (window.innerWidth || 1280) / 1280);
}

/** Auto: the size for this screen.  The desk is laid out for 1280-1920 px
 *  (a 1080p laptop at 100 or 125 %, a 4K laptop at 250 %): there it is
 *  100 %.  A bigger screen (a 1440p or 4K monitor at 100 %) scales up with
 *  it, to 200 %, so text and buttons stay the same size to the eye; a
 *  smaller window (1280 x 720 and under) scales down to fit, to 80 %. */
export function autoScale(w = window.innerWidth || 1280, h = window.innerHeight || 800) {
  const up = Math.min(w / 1920, h / 1080);
  const z = up > 1 ? Math.min(2, up) : Math.max(0.8, Math.min(1, w / 1280, h / 720));
  return Math.floor(z * 20) / 20;                           // in steps of 5 %
}

// the whole screen bigger or smaller (text and controls); the 3D's picking
// reads the canvas's on-screen box, so it stays right
let zoomNow = 1;
function setZoom(want) {
  const z = want === "auto" ? autoScale() : want > 1 ? Math.min(want, maxScale()) : want;
  if (z === zoomNow && document.documentElement.style.getPropertyValue("--ui-zoom")) return false;
  zoomNow = z;
  const root = document.documentElement;
  root.style.zoom = z === 1 ? "" : String(z);
  root.style.setProperty("--ui-zoom", String(z));
  return true;
}
// a window made narrower (or wider) gets the size that fits it
window.addEventListener("resize", () => { if (setZoom(themeNow().scale)) window.dispatchEvent(new Event("resize")); });

export function setTheme(patch) {
  const t = { ...themeNow(), ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(t)); } catch (e) { /* private window */ }
  applyTheme(t);
  return t;
}

/** Back to how the desk comes (this computer). */
export function resetTheme() {
  try { localStorage.removeItem(KEY); } catch (e) { /* private window */ }
  applyTheme();
}

export function initTheme() { applyTheme(); }
