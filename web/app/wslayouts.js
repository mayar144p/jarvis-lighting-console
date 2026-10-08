// The workspaces' data (workspaces.js has the screen): the ready ones and
// the check that keeps a saved one sane.  No screen here, so the selftest
// loads it in node.

export const MIN_W = 220, MAX_W = 620;
export const BOTTOMS = ["faders", "buttons", "timeline"];

// the ready ones: what a job wants on screen
export const BUILTIN = [
  { id: "programming", name: "Programming", fix: true, prog: true, swap: false, fixW: 0, progW: 0, bottom: "faders", dock: 0, tab: "" },
  { id: "busking", name: "Busking", fix: false, prog: true, swap: false, fixW: 0, progW: 0, bottom: "buttons", dock: 0, tab: "" },
  { id: "theatre", name: "Theatre (cues)", fix: true, prog: true, swap: false, fixW: 0, progW: 0, bottom: "faders", dock: 300, tab: "" },
  { id: "show", name: "Show (run only)", fix: false, prog: false, swap: false, fixW: 0, progW: 0, bottom: "buttons", dock: 0, tab: "" },
];

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
  };
}
