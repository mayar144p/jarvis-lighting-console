// How the desk's screen looks, per computer (backlog A10 item 10):
//   Show dark - the panels in dim red, so eyes stay used to a dark venue
//               (the 3D view keeps its true colours: it shows the lights);
//   accent    - what "lit" is: the colour of what is selected or switched
//               on.  The desk comes with a warm white (a lit key); a colour
//               can be picked instead;
//   panels    - their brightness (the 3D has its own House slider).
const KEY = "haze.theme";
export const ACCENTS = {
  lit: ["#f1ede2", "#d9d5ca"],
  haze: ["#35d0ee", "#1fb9d8"], blue: ["#5aa3ee", "#3f8be0"], green: ["#4fd38f", "#36b877"], amber: ["#f0a93a", "#d99322"],
  red: ["#ff6b6b", "#ef4444"], pink: ["#f078b6", "#dd5d9f"], violet: ["#a88cf0", "#9176e0"],
};
// how each one is called in Settings
export const ACCENT_NAMES = { lit: "Lit white (how the desk comes)", haze: "Haze cyan", blue: "Blue", green: "Green", amber: "Amber", red: "Red", pink: "Pink", violet: "Violet" };
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

/** The legend that reads on a colour: near-black, or white on a dark one. */
function ink(hex) {
  const n = parseInt(hex.slice(1), 16);
  return 0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255) > 140 ? "#141516" : "#ffffff";
}

export function themeNow() {
  let t = {};
  try { t = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { t = {}; }
  // cyan (and violet before it) was the standard before the redesign: a desk
  // still on one of them moves to lit white once.  A colour picked after
  // that stays (v3 says the move was made).
  if (!t.v3) {
    if (!t.accent || t.accent === "haze" || t.accent === "violet" || t.accent === "cyan" || t.accent === "white") t.accent = "lit";
    t.v3 = 1;
    if (Object.keys(t).length > 1) { try { localStorage.setItem(KEY, JSON.stringify(t)); } catch (e) { /* fine */ } }
  }
  return { mode: t.mode === "showdark" ? "showdark" : "normal",
    accent: ACCENTS[t.accent] || HEX.test(String(t.accent)) ? t.accent : "lit",
    bright: Math.max(0.3, Math.min(1, +t.bright || 1)),
    density: DENSITIES[t.density] ? t.density : "standard",
    text: TEXT_SCALE[t.text] ? t.text : "standard",
    motion: t.motion === "reduced" ? "reduced" : "full", v3: 1 };
}

export function applyTheme(t = themeNow()) {
  const root = document.documentElement;
  root.dataset.theme = t.mode;
  root.dataset.density = t.density;
  root.dataset.textsize = t.text;
  root.dataset.motion = t.motion;
  // lit: the stylesheet's own value (css/tokens.css) unless a colour was picked
  if (t.accent === "lit") {
    for (const k of ["--lit", "--lit-2", "--lit-ink"]) root.style.removeProperty(k);
  } else {
    const [a, a2] = ACCENTS[t.accent] || [t.accent, shade(t.accent)];
    root.style.setProperty("--lit", a);
    root.style.setProperty("--lit-2", a2);
    root.style.setProperty("--lit-ink", ink(a));
  }
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
