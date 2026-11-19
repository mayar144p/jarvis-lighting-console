// The three modes' data (workspaces.js has the screen): what each mode puts
// in the frame, and the check that keeps what a computer saved sane.  No
// screen here, so the selftest loads it in node.

export const MIN_W = 220, MAX_W = 620;
// the dock's tabs, in the order they sit ("faders" is the Cues tab: the
// playbacks with their faders, and the cue list of the one in hand)
export const BOTTOMS = ["buttons", "faders", "timeline", "groups"];
export const BOTTOM_NAMES = { buttons: "Buttons", faders: "Cues", timeline: "Timeline", groups: "Group faders" };

// The three modes: each job has its screen, in one frame - the lights on
// the left, the work in the middle, the master on the right.
//   setup    the lights, the room, the connections, the checks: nothing is
//            played yet, so there is no dock
//   program  the programmer over the dock (buttons, cues, timeline, group
//            faders - one at a time)
//   live     the show running: the dock is the screen - the button page,
//            the group faders under it, what plays now above it
export const MODES = ["setup", "program", "live"];
export const MODE_NAMES = { setup: "Set up", program: "Program", live: "Live" };

const width = (v) => { v = Math.round(+v || 0); return v ? Math.max(MIN_W, Math.min(MAX_W, v)) : 0; };
const bottom = (v) => (BOTTOMS.includes(v) ? v : "buttons");

/** What this computer keeps about the screen, every field the right type
 *  (a damaged entry can't break the screen): the mode, the side panel's
 *  width, the programmer's tab, and per mode the dock's tab - in Program
 *  also whether the dock has the whole work area. */
export function clean(raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  const by = r.by && typeof r.by === "object" ? r.by : {};
  const of = (m) => (by[m] && typeof by[m] === "object" ? by[m] : {});
  return {
    mode: MODES.includes(r.mode) ? r.mode : "program",
    sideW: width(r.sideW),
    workW: width(r.workW),
    tab: /^[a-z]{1,20}$/.test(r.tab || "") ? r.tab : "",
    by: {
      program: { bottom: bottom(of("program").bottom), max: !!of("program").max },
      live: { bottom: bottom(of("live").bottom) },
    },
  };
}

/** What a mode puts in the frame (the same frame in every mode):
 *    side        the lights on the left - not while a show is played
 *    programmer  Program's work area (not when the dock has it all)
 *    hub         Set up's own screen, where the programmer is in Program
 *    now         Live: what plays now, above the dock, only while something does
 *    dock        buttons / cues / timeline / group faders; none in Set up
 *    max         the dock has the whole work area (always in Live)
 *    bottom      the dock's tab
 *    strip       Live: the group faders as a strip under every tab (so the
 *                dock has no Group faders tab there)
 *  The master rail (grand master, release, blackout) is in every mode. */
export function frameFor(saved, mode) {
  const s = clean(saved);
  const m = MODES.includes(mode) ? mode : s.mode;
  const live = m === "live", setup = m === "setup", mem = s.by[m] || {};
  const max = live || (m === "program" && !!mem.max);
  let tab = bottom(mem.bottom);
  if (live && tab === "groups") tab = "buttons";
  return {
    // (Set up is for patching: its patch lists the lights, and no master -
    // the X key still blacks out)
    mode: m, side: m === "program", programmer: m === "program" && !max, hub: setup, now: live,
    dock: !setup, max, bottom: tab, strip: live, rail: !setup,
  };
}
