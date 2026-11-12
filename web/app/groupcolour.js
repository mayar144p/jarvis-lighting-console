// A group's colour: its fader on the desk's dock and on the phone remote
// (web/remote/remote.js imports this too, so both show the same colour).
// One chosen for it (group_edit colour), or its own by its place in the list.

// (twelve: a show's groups rarely run past that, so neighbours don't repeat)
export const GROUP_TINTS = ["#38bdf8", "#22c55e", "#f59e0b", "#ef4444", "#a78bfa", "#ec4899",
  "#14b8a6", "#eab308", "#f97316", "#84cc16", "#e879f9", "#94a3b8"];

/** The colour of the group at index `i` of the show's groups. */
export const groupColour = (g, i) => (g && g.colour) || GROUP_TINTS[((i % GROUP_TINTS.length) + GROUP_TINTS.length) % GROUP_TINTS.length];
