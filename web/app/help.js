// Help built in (backlog A10 item 11): hover any control and a short
// explanation shows - quicker and easier to read than the browser's own
// tooltip, and controls that had none get one here.  Off in Settings ->
// Screen & 3D.  The control's title stays its title (it's only borrowed
// while the pointer is on it), so screen readers and the checks still read it.
import { $ } from "./ui.js";

const KEY = "jarvis.tips";
// controls that had no explanation of their own
const HELP = {
  "out-arm": "Go live: the real lights get the desk. Until then BLIND - nothing leaves this computer.",
  "add-btn": "Add lights from the library (or a GDTF, or from a light's manual).",
  "add-btn-2": "Add lights from the library (or a GDTF, or from a light's manual).",
  "fx-filter": "Find lights by name, type, brand or DMX address (U1.17).",
  house: "House lights: how lit the room is in the 3D view (not the rig).",
  haze: "Haze in the 3D view: more haze, more visible beams.",
  "int-fade": "Fade: seconds the level takes to get there.",
  "hex-in": "A colour as #rrggbb.",
  kelvin: "White by colour temperature: low warm, high cool.",
  "aim-pan": "Pan in degrees for Aim.", "aim-tilt": "Tilt in degrees for Aim.",
  "aim-btn": "Point the selection at that pan and tilt.",
  "lfo-attr": "Which attribute the effect moves.", "lfo-wave": "The effect's shape: sine, square, saw...",
  "lfo-speed": "Effect speed (cycles a second).", "lfo-spread": "Offset between lights: 0 together, 100 spread across.",
  "lfo-run": "Start this effect on the selection.",
  "look-search": "Find a saved look or palette.",
  "arr-axis": "Which way Align / Spread / Mirror works.",
  "fan-attr": "What to fan across the selection.", "fan-lo": "The first light's value.", "fan-hi": "The last light's value.",
  "fan-mode": "Fan from one end, from the middle, or out from the centre.", "fan-btn": "Spread the values across the selection.",
  "lim-role": "Which attribute to limit.", "lim-lo": "The lowest it may go.", "lim-hi": "The highest it may go.",
  "lim-set": "Keep this attribute between those values on these lights - every cue and effect too.",
  "lim-clear": "Remove the limit.",
  "sel-similar": "Select every light of the same type as the first one selected.",
  "qb-quant": "Buttons fire on the next beat or bar of the tempo.",
  "tl-bpm": "The timeline's tempo.", "tl-snap": "What clips snap to on the timeline.",
  "cmd-input": "Console syntax (1 thru 4 @ 50) runs at once; anything else goes to the copilot.",
  "ai-send": "Send to the copilot: it shows the plan in 3D before anything changes.",
  "auto-btn": "Design a whole show for this rig, on the timeline.",
  "design-btn": "Three show concepts to choose from.", "doctor-btn": "Check the rig, patch and output for problems.",
  "gm-fader": "Grand master: every light's level, last in line.",
  "bo-btn": "Blackout (X): everything off at once.",
};
let tip = null, timer = 0, held = null;

const enabled = () => { try { return localStorage.getItem(KEY) !== "0"; } catch (e) { return true; } };
export const tipsOn = enabled;
export function setTips(on) { try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) { /* private */ } hide(); }

function textOf(el) {
  return (el.dataset.tip || el.getAttribute("title") || HELP[el.id] || "").trim();
}

function hide() {
  clearTimeout(timer);
  if (held) { if (held.dataset.tip && !held.hasAttribute("title")) held.setAttribute("title", held.dataset.tip); delete held.dataset.tip; held = null; }
  if (tip) tip.hidden = true;
}

function show(el, text) {
  if (!tip) { tip = document.createElement("div"); tip.className = "help-tip"; tip.setAttribute("role", "tooltip"); document.body.append(tip); }
  tip.textContent = text;
  tip.hidden = false;
  const r = el.getBoundingClientRect(), w = tip.offsetWidth, ht = tip.offsetHeight;
  let top = r.bottom + 8;
  if (top + ht > innerHeight - 6) top = r.top - ht - 8;
  tip.style.left = `${Math.max(6, Math.min(innerWidth - w - 6, r.left + r.width / 2 - w / 2))}px`;
  tip.style.top = `${Math.max(6, top)}px`;
}

export function initHelp() {
  if (navigator.webdriver) return;                  // the automated screen checks
  // fill in the controls that had nothing to say
  for (const [id, text] of Object.entries(HELP)) {
    const el = document.getElementById(id);
    if (el && !el.getAttribute("title") && !el.getAttribute("aria-description")) el.setAttribute("aria-description", text);
  }
  document.addEventListener("pointerover", (e) => {
    if (!enabled() || e.pointerType === "touch") return;
    const el = e.target.closest && e.target.closest("[title], button, input, select, [data-help]");
    if (!el || el === held) return;
    hide();
    const text = textOf(el);
    if (!text) return;
    held = el;
    if (el.hasAttribute("title")) { el.dataset.tip = el.getAttribute("title"); el.removeAttribute("title"); }   // no double tooltip
    timer = setTimeout(() => { if (held === el && el.isConnected) show(el, text); }, 420);
  });
  document.addEventListener("pointerout", (e) => { if (held && !held.contains(e.relatedTarget)) hide(); });
  document.addEventListener("pointerdown", hide, true);
  document.addEventListener("keydown", hide, true);
  addEventListener("blur", hide);
}
