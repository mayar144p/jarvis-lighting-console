// The workspaces' data (workspaces.js has the screen): the ready ones and
// the check that keeps a saved one sane.  No screen here, so the selftest
// loads it in node.

export const MIN_W = 220, MAX_W = 620;
export const BOTTOMS = ["faders", "buttons", "timeline"];

// the ready ones: what a job wants on screen
export const BUILTIN = [
  { id: "programming", name: "Programming", fix: true, prog: true, swap: false, fixW: 0, progW: 0, bottom: "faders", dock: 0, tab: "", fixAt: "left", progAt: "right", dockAt: "bottom", fixPos: { x: 16, y: 16 }, progPos: { x: 340, y: 16 } },
  { id: "busking", name: "Busking", fix: false, prog: true, swap: false, fixW: 0, progW: 0, bottom: "buttons", dock: 0, tab: "", fixAt: "left", progAt: "right", dockAt: "bottom", fixPos: { x: 16, y: 16 }, progPos: { x: 340, y: 16 } },
  { id: "theatre", name: "Theatre (cues)", fix: true, prog: true, swap: false, fixW: 0, progW: 0, bottom: "faders", dock: 300, tab: "", fixAt: "left", progAt: "right", dockAt: "bottom", fixPos: { x: 16, y: 16 }, progPos: { x: 340, y: 16 } },
  { id: "show", name: "Show (run only)", fix: false, prog: false, swap: false, fixW: 0, progW: 0, bottom: "buttons", dock: 0, tab: "", fixAt: "left", progAt: "right", dockAt: "bottom", fixPos: { x: 16, y: 16 }, progPos: { x: 340, y: 16 } },
];

export const SIDES = ["left", "right"];
// where a side panel can be: a side of the screen, or floating over the 3D
export const PLACES = [...SIDES, "float"];

/** Where the panels go: {cols, rows, areas, fixAt, progAt, top} for the
 *  workspace grid.  Each side panel on the left or the right (both on one
 *  side works: the fixture list on the outside), the dock at the bottom or
 *  the top.  A hidden or floating panel takes no column. */
export function gridFor(w) {
  const fixAt = PLACES.includes(w.fixAt) ? w.fixAt : (w.swap ? "right" : "left");
  const progAt = PLACES.includes(w.progAt) ? w.progAt : (w.swap ? "left" : "right");
  const cols = { fixtures: "var(--fixtures-w)", prog: "var(--prog-w)" };
  const left = [], right = [];
  // a floating panel takes no column: it sits over the 3D view
  if (w.fix !== false && fixAt !== "float") (fixAt === "left" ? left : right).push("fixtures");
  if (w.prog !== false && progAt !== "float") (progAt === "left" ? left : right).push("prog");
  // two on one side: the fixture list at the screen's edge
  left.sort((a) => (a === "fixtures" ? -1 : 1));
  right.sort((a) => (a === "fixtures" ? 1 : -1));
  const names = [...left, "stage", ...right];
  const main = `"${names.join(" ")}"`, dock = `"${names.map(() => "pb").join(" ")}"`;
  const top = w.dockAt === "top";
  return {
    cols: [...left.map((n) => cols[n]), "minmax(0, 1fr)", ...right.map((n) => cols[n])].join(" "),
    rows: top ? "var(--pb-h) minmax(0, 1fr)" : "minmax(0, 1fr) var(--pb-h)",
    areas: top ? `${dock} ${main}` : `${main} ${dock}`,
    fixAt, progAt, top,
  };
}

const pos = (p, x) => {
  const n = (v, d) => (Number.isFinite(+v) ? Math.max(0, Math.min(4000, Math.round(+v))) : d);
  return { x: n(p && p.x, x), y: n(p && p.y, 16) };
};

/** A workspace with every field the right type (a damaged entry can't break the screen). */
export function clean(w) {
  const width = (v) => { v = Math.round(+v || 0); return v ? Math.max(MIN_W, Math.min(MAX_W, v)) : 0; };
  return {
    id: String(w.id || "").slice(0, 40), name: String(w.name || "Workspace").slice(0, 40),
    fix: w.fix !== false, prog: w.prog !== false, swap: !!w.swap,
    fixW: width(w.fixW), progW: width(w.progW),
    bottom: BOTTOMS.includes(w.bottom) ? w.bottom : "faders",
    dock: Math.max(0, Math.min(1200, Math.round(+w.dock || 0))),
    tab: /^[a-z]{1,20}$/.test(w.tab || "") ? w.tab : "",
    fixAt: PLACES.includes(w.fixAt) ? w.fixAt : (w.swap ? "right" : "left"),
    progAt: PLACES.includes(w.progAt) ? w.progAt : (w.swap ? "left" : "right"),
    dockAt: w.dockAt === "top" ? "top" : "bottom",
    // a floating panel's top-left corner over the 3D view, in px
    fixPos: pos(w.fixPos, 16), progPos: pos(w.progPos, 340),
  };
}
