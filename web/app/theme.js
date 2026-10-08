// How the desk's screen looks, per computer (backlog A10 item 10):
//   Show dark - the panels in dim red, so eyes stay used to a dark venue
//               (the 3D view keeps its true colours: it shows the lights);
//   accent    - the highlight colour;
//   panels    - their brightness (the 3D has its own House slider).
const KEY = "jarvis.theme";
export const ACCENTS = {
  violet: ["#8b8dff", "#7577f5"], blue: ["#4ea1ff", "#2f86ef"], green: ["#3dd68c", "#25b873"],
  amber: ["#f5a524", "#dd8f10"], pink: ["#f472b6", "#e2559c"],
};

export function themeNow() {
  let t = {};
  try { t = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { t = {}; }
  return { mode: t.mode === "showdark" ? "showdark" : "normal", accent: ACCENTS[t.accent] ? t.accent : "violet",
    bright: Math.max(0.3, Math.min(1, +t.bright || 1)) };
}

export function applyTheme(t = themeNow()) {
  const root = document.documentElement;
  root.dataset.theme = t.mode;
  const [a, a2] = ACCENTS[t.accent] || ACCENTS.violet;
  root.style.setProperty("--accent", a);
  root.style.setProperty("--accent-2", a2);
  root.style.setProperty("--accent-bg", a + "24");
  const b = t.mode === "showdark" ? Math.min(t.bright, 0.75) : t.bright;
  root.style.setProperty("--panel-filter", t.mode === "showdark"
    ? `brightness(${b}) sepia(1) saturate(6) hue-rotate(-48deg)`
    : b < 0.999 ? `brightness(${b})` : "none");
}

export function setTheme(patch) {
  const t = { ...themeNow(), ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(t)); } catch (e) { /* private window */ }
  applyTheme(t);
  return t;
}

export function initTheme() { applyTheme(); }
