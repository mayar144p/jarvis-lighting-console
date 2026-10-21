// The workspaces' data (workspaces.js has the screen): the ready ones and
// the check that keeps a saved one sane.  No screen here, so the selftest
// loads it in node.

export const MIN_W = 220, MAX_W = 620;
export const BOTTOMS = ["faders", "buttons", "timeline"];

// The three modes: each job has its screen.
//   setup    lights, room, connections, checks: the fixture list, the 3D and
//            the Set up hub (no faders: nothing is being played yet)
//   program  the programmer, cue lists, palettes, effects, buttons, timeline
//   live     the show running: the Live view, the 3D, buttons, master
export const MODES = ["setup", "program", "live"];
export const MODE_NAMES = { setup: "Set up", program: "Program", live: "Live" };

// the ready ones: what a job wants on screen (fewer than before: Busking is
// Programming with the buttons, Show is Live; "No 3D" is for a small
// laptop or a desk whose 3D isn't wanted)
export const BUILTIN = [
  { id: "setup", name: "Set up", mode: "setup", fix: true, prog: true, stage: true, swap: false, fixW: 0, progW: 400, bottom: "faders", dock: 0, tab: "", cues: false, fixAt: "left", progAt: "right", dockAt: "bottom", fixPos: { x: 16, y: 16 }, progPos: { x: 340, y: 16 } },
  { id: "live", name: "Live", mode: "live", fix: false, prog: true, stage: true, swap: false, fixW: 0, progW: 460, bottom: "buttons", dock: 0, tab: "", cues: false, fixAt: "left", progAt: "right", dockAt: "bottom", fixPos: { x: 16, y: 16 }, progPos: { x: 340, y: 16 } },
  { id: "programming", name: "Programming", mode: "program", fix: true, prog: true, stage: true, swap: false, fixW: 0, progW: 0, bottom: "faders", dock: 0, tab: "", cues: false, fixAt: "left", progAt: "right", dockAt: "bottom", fixPos: { x: 16, y: 16 }, progPos: { x: 340, y: 16 } },
  { id: "theatre", name: "Theatre (cues)", mode: "program", fix: true, prog: true, stage: true, swap: false, fixW: 0, progW: 0, bottom: "faders", dock: 0, tab: "", cues: true, fixAt: "left", progAt: "right", dockAt: "bottom", fixPos: { x: 16, y: 16 }, progPos: { x: 340, y: 16 } },
  { id: "nostage", name: "No 3D (buttons + programmer)", mode: "program", fix: true, prog: true, stage: false, swap: false, fixW: 0, progW: 0, bottom: "buttons", dock: 0, tab: "", cues: false, fixAt: "left", progAt: "right", dockAt: "bottom", fixPos: { x: 16, y: 16 }, progPos: { x: 340, y: 16 } },
];

export const SIDES = ["left", "right"];
// where a side panel can be: a side of the screen, or floating over the 3D
export const PLACES = [...SIDES, "float"];

/** Where the panels go: {cols, rows, areas, fixAt, progAt, top} for the
 *  workspace grid.  Each side panel on the left or the right (both on one
 *  side works: the fixture list on the outside), the dock at the bottom or
 *  the top.  A hidden or floating panel takes no column. */
export function gridFor(w) {
  const noStage = w.stage === false;
  let fixAt = PLACES.includes(w.fixAt) ? w.fixAt : (w.swap ? "right" : "left");
  let progAt = PLACES.includes(w.progAt) ? w.progAt : (w.swap ? "left" : "right");
  // nothing to float over without the 3D: the panels go to their sides
  if (noStage && fixAt === "float") fixAt = "left";
  if (noStage && progAt === "float") progAt = "right";
  const cols = { fixtures: "var(--fixtures-w)", prog: "var(--prog-w)" };
  const left = [], right = [];
  // a floating panel takes no column: it sits over the 3D view
  if (w.fix !== false && fixAt !== "float") (fixAt === "left" ? left : right).push("fixtures");
  if (w.prog !== false && progAt !== "float") (progAt === "left" ? left : right).push("prog");
  // two on one side: the fixture list at the screen's edge
  left.sort((a) => (a === "fixtures" ? -1 : 1));
  right.sort((a) => (a === "fixtures" ? 1 : -1));
  const top = w.dockAt === "top";
  if (noStage) {
    // no 3D: the programmer (else the fixture list) takes the room it had;
    // with neither, the dock fills the screen
    const names = [...left, ...right];
    const grow = names.includes("prog") ? "prog" : names[0];
    const colsNo = names.map((n) => (n === grow ? "minmax(0, 1fr)" : cols[n])).join(" ");
    if (!names.length) return { cols: "minmax(0, 1fr)", rows: "minmax(0, 1fr)", areas: '"pb"', fixAt, progAt, top: false };
    const main = `"${names.join(" ")}"`, dock = `"${names.map(() => "pb").join(" ")}"`;
    if (w.mode === "setup") return { cols: colsNo, rows: "minmax(0, 1fr)", areas: main, fixAt, progAt, top: false };
    return { cols: colsNo, rows: top ? "var(--pb-h) minmax(0, 1fr)" : "minmax(0, 1fr) var(--pb-h)",
      areas: top ? `${dock} ${main}` : `${main} ${dock}`, fixAt, progAt, top };
  }
  const names = [...left, "stage", ...right];
  const main = `"${names.join(" ")}"`, dock = `"${names.map(() => "pb").join(" ")}"`;
  if (w.mode === "setup") {
    // setting up plays nothing: no faders or buttons, the room gets the height
    return { cols: [...left.map((n) => cols[n]), "minmax(0, 1fr)", ...right.map((n) => cols[n])].join(" "),
      rows: "minmax(0, 1fr)", areas: main, fixAt, progAt, top: false };
  }
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
    mode: MODES.includes(w.mode) ? w.mode : "program",
    fix: w.fix !== false, prog: w.prog !== false, stage: w.stage !== false, swap: !!w.swap,
    fixW: width(w.fixW), progW: width(w.progW),
    bottom: BOTTOMS.includes(w.bottom) ? w.bottom : "faders",
    dock: Math.max(0, Math.min(1200, Math.round(+w.dock || 0))),
    tab: /^[a-z]{1,20}$/.test(w.tab || "") ? w.tab : "",
    // the cue list of the playback in focus, beside the faders (Theatre)
    cues: !!w.cues,
    fixAt: PLACES.includes(w.fixAt) ? w.fixAt : (w.swap ? "right" : "left"),
    progAt: PLACES.includes(w.progAt) ? w.progAt : (w.swap ? "left" : "right"),
    dockAt: w.dockAt === "top" ? "top" : "bottom",
    // a floating panel's top-left corner over the 3D view, in px
    fixPos: pos(w.fixPos, 16), progPos: pos(w.progPos, 340),
  };
}
