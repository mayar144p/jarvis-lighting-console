// Pick a group fader's colour (the desk's dock, the full-screen buttons'
// sliders and the phone remote all show it): eight to choose from, any
// other colour, or its own again.
import { menu, h } from "./ui.js";
import { run } from "./actions.js";
import { GROUP_TINTS, groupColour } from "./groupcolour.js";

const NAMES = { "#38bdf8": "Sky blue", "#22c55e": "Green", "#f59e0b": "Amber", "#ef4444": "Red",
  "#a78bfa": "Lavender", "#ec4899": "Pink", "#14b8a6": "Teal", "#eab308": "Yellow",
  "#f97316": "Orange", "#84cc16": "Lime", "#e879f9": "Violet", "#94a3b8": "Grey" };

export function groupColourMenu(anchor, g, index) {
  const now = groupColour(g, index);
  const dot = (c) => h("span", { style: { display: "inline-block", width: "14px", height: "14px", borderRadius: "50%",
    background: c, marginRight: "8px", verticalAlign: "-2px", boxShadow: c === now ? "0 0 0 2px var(--text)" : "none" } });
  const set = (c) => run("group_edit", { group: g.n, colour: c }, { toast: false });
  menu(anchor, [
    ...GROUP_TINTS.map((c) => ({ label: h("span", dot(c), NAMES[c] || c), hint: c === now ? "now" : "", run: () => set(c) })),
    "-",
    { label: "Any colour…", run: () => {
      const pick = h("input", { type: "color", value: now, style: { position: "fixed", left: "-100px", opacity: "0" } });
      pick.addEventListener("change", () => { set(pick.value); pick.remove(); });
      pick.addEventListener("blur", () => setTimeout(() => pick.remove(), 500));
      document.body.append(pick);
      pick.click();
    } },
    g.colour ? { label: "Its own colour again", run: () => set("") } : null,
  ]);
}
