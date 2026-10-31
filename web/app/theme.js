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
// text size on top of the screen's own size class: the layout grows with it
export const TEXT_SCALE = { standard: "Standard", large: "Large", xl: "Extra large" };
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
  return { mode: t.mode === "showdark" ? "showdark" : "normal",
    accent: ACCENTS[t.accent] || HEX.test(String(t.accent)) ? t.accent : "violet",
    bright: Math.max(0.3, Math.min(1, +t.bright || 1)),
    density: DENSITIES[t.density] ? t.density : "standard",
    text: TEXT_SCALE[t.text] ? t.text : "standard",
    motion: t.motion === "reduced" ? "reduced" : "full" };
}

export function applyTheme(t = themeNow()) {
  const root = document.documentElement;
  root.dataset.theme = t.mode;
  root.dataset.density = t.density;
  root.dataset.textsize = t.text;
  root.dataset.motion = t.motion;
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
