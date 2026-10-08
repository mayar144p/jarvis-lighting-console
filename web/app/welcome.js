// A new desk's first minute (backlog A10 item 2): a welcome with the demo
// show (lights moving in seconds), a show template (a room, real lights,
// groups, palettes, buttons and cues - the show starts 80 % done) or an
// empty show; New show… from the Show menu; and a short tour of the desk.
import { state, on } from "./store.js";
import { run } from "./actions.js";
import { $, h, modal, toast } from "./ui.js";

const SEEN = "jarvis.welcomed";
// 24x24 stroke icons (as the buttons' icons), not emoji
const ICON = {
  club: "M9 18V5l12-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM21 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
  wedding: "M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z",
  band: "M13 2 4 14h7l-1 8 9-12h-7z",
  theatre: "m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z",
  corporate: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3",
};
function tplIcon(id) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "tpl-icon");
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", ICON[id] || ICON.band);
  svg.append(p);
  return svg;
}
const seen = () => { try { return localStorage.getItem(SEEN) === "1"; } catch { return true; } };
const markSeen = () => { try { localStorage.setItem(SEEN, "1"); } catch { /* private window */ } };

async function templates() {
  const r = await run("show_templates", {}, { silentError: true });
  return (r && r.templates) || [];
}

async function startFrom(id, demo = false) {
  toast(demo ? "Opening the demo…" : "Building the show…", "", 1800);
  const r = await run("show_template", { name: id, demo });
  if (!r.ok) return false;
  toast(`${r.summary}${demo ? " - playing" : ""}. One Ctrl+Z brings back what was there.`, "ok", 5000);
  return true;
}

function cards(list, pick) {
  return h("div.tpl-grid", ...list.map((t) => h("button.tpl-card", { type: "button", onclick: () => pick(t.id) },
    tplIcon(t.id),
    h("b", t.label), h("span.small.muted", t.blurb), h("span.tpl-meta", `${t.lights} lights · ${t.venue.replace("_", " ")}`))));
}

/** Show ▾ -> New show…: a template, or empty. */
export async function openNewShow() {
  const list = await templates();
  const patched = ((state.snap && state.snap.patch) || []).length;
  let close = null;
  const pick = async (id) => { close(); await startFrom(id); };
  close = modal({
    title: "New show", wide: true,
    body: h("div.tpl",
      h("p.muted.small", patched ? "The show that's open now goes (save it first if you want to keep it - one Ctrl+Z also brings it back)." : "Pick the kind of show: a room, lights, groups, palettes, buttons and cues, ready to change."),
      cards(list, pick)),
    foot: [h("button.btn", { onclick: async () => { close(); const r = await run("show_new", {}); if (r.ok) toast("An empty show", "ok"); } }, "Empty show"),
      h("span.grow"), h("button.btn", { onclick: () => close() }, "Cancel")],
  });
}

/** The first time a desk opens with nothing patched. */
function welcome() {
  markSeen();
  let close = null;
  const go = async (fn) => { close(); await fn(); offerTour(); };
  templates().then((list) => {
    close = modal({
      title: "Welcome to Jarvis", wide: true,
      body: h("div.tpl",
        h("div.tpl-hero",
          h("button.btn.primary.tpl-demo", { onclick: () => go(() => startFrom("club", true)) }, "▶ Show me the demo"),
          h("span.muted.small", "A club night already programmed, playing in the 3D view. Nothing leaves this computer.")),
        h("h3", "Or start a show from a template"),
        cards(list, (id) => go(() => startFrom(id))),
        h("p.muted.small", "Every template is a normal show: change, add or delete anything.")),
      foot: [h("button.btn", { onclick: () => { close(); offerTour(); } }, "Start empty"), h("span.grow"),
        h("button.btn.ghost", { onclick: () => { close(); startTour(); } }, "Take the tour first")],
    });
  });
}

function offerTour() {
  try { if (localStorage.getItem("jarvis.toured") === "1") return; } catch { return; }
  toast("New here? Help (?) → Take the tour shows you round in a minute.", "", 6000);
}

// ---------------------------------------------------------------- the tour
const STEPS = [
  ["#show-btn", "Shows", "Save, open and start shows here - New show… has the templates, and MVR plots come in here too."],
  ["#output", "Output", "BLIND means nothing reaches the real lights. Go live… when the rig is connected."],
  ["#fixtures-panel", "Fixtures", "Your lights and groups. Click to select; + Add finds any light in the library."],
  ["#stage-wrap", "The 3D view", "What the lights are doing, live. Drag to look around, click a light to select it, Arrange to move things."],
  ["#programmer", "Programmer", "Level, colour, position, beam and effects for the selected lights."],
  ["#playbacks", "Playbacks and buttons", "Cue stacks with GO, a page of instant buttons, and the timeline. BLACKOUT and the grand master on the right."],
  ["#ai-btn", "Copilot", "Say what you want - “slow blue wash on the movers” - and the AI does it, in 3D first."],
];

/** A short tour: a highlight round each part of the desk, Next / Back. */
export function startTour() {
  try { localStorage.setItem("jarvis.toured", "1"); } catch { /* private */ }
  const steps = STEPS.filter(([sel]) => { const el = $(sel); return el && el.getClientRects().length; });
  if (!steps.length) return;
  let i = 0;
  const ring = h("div.tour-ring", { "aria-hidden": "true" });
  const bubble = h("div.tour-bubble", { role: "dialog", "aria-live": "polite" });
  const shade = h("div.tour-shade");
  const end = () => { shade.remove(); ring.remove(); bubble.remove(); removeEventListener("keydown", keys, true); removeEventListener("resize", show); };
  const keys = (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); end(); }
    else if (e.key === "ArrowRight" || e.key === "Enter") { e.preventDefault(); e.stopPropagation(); next(1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); next(-1); }
  };
  const next = (d) => { i += d; if (i >= steps.length) end(); else { i = Math.max(0, i); show(); } };
  function show() {
    const [sel, title, text] = steps[i];
    const r = $(sel).getBoundingClientRect();
    Object.assign(ring.style, { left: `${r.left - 6}px`, top: `${r.top - 6}px`, width: `${r.width + 12}px`, height: `${r.height + 12}px` });
    bubble.replaceChildren(h("small.muted", `${i + 1} of ${steps.length}`), h("b", title), h("p", text),
      h("div.row-btns", h("button.btn.small.ghost", { onclick: end }, "Skip"), h("span.grow"),
        i ? h("button.btn.small", { onclick: () => next(-1) }, "Back") : null,
        h("button.btn.small.primary", { onclick: () => next(1) }, i === steps.length - 1 ? "Done" : "Next")));
    // beside the part, inside the window
    const bw = 300, bh = bubble.offsetHeight || 150;
    let left = r.right + 14, top = r.top;
    if (left + bw > innerWidth - 8) left = r.left - bw - 14;
    if (left < 8) { left = Math.min(Math.max(8, r.left), innerWidth - bw - 8); top = r.bottom + 14; }
    if (top + bh > innerHeight - 8) top = Math.max(8, r.top - bh - 14);
    Object.assign(bubble.style, { left: `${left}px`, top: `${Math.max(8, Math.min(top, innerHeight - bh - 8))}px`, width: `${bw}px` });
    bubble.querySelector("button.primary").focus();
  }
  document.body.append(shade, ring, bubble);
  addEventListener("keydown", keys, true);
  addEventListener("resize", show);
  show();
}

/** Called once at boot: the welcome on a desk that's new and empty. */
export function initWelcome() {
  const q = new URLSearchParams(location.search);
  // not in another window of the desktop app, and not under the automated
  // screen checks (they start empty every time) unless they ask for it
  if (q.get("window") || (navigator.webdriver && !q.has("welcome")) || (seen() && !q.has("welcome"))) return;
  const off = on("snapshot", () => {
    off && off();
    const s = state.snap || {};
    if (!(s.patch || []).length && !s.show_file) setTimeout(welcome, 400);
    else markSeen();                    // a desk already in use: never
  });
}
