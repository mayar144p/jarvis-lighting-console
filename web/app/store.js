// The one client-side copy of the engine's state, and change events.
//
// `snapshot` replaces everything (on connect), `snapdiff` the parts an
// edit changed;
// `lite` refreshes the fast-moving parts; `looks` is the light for the
// stage.  Panels subscribe to the slices they draw.

const listeners = new Map();

export const state = {
  connected: false,
  linkError: null,
  snap: null,                 // last full snapshot
  lite: null,                 // last fast update
  looks: {},                  // head -> look row (lit heads only)
  status: null,               // /api/status (config, AI, fixture count)
  lastAction: "",
};

export function on(topic, fn) {
  if (!listeners.has(topic)) listeners.set(topic, new Set());
  listeners.get(topic).add(fn);
  return () => listeners.get(topic).delete(fn);
}

export function emit(topic, payload) {
  for (const fn of listeners.get(topic) || []) {
    try { fn(payload); } catch (e) { console.error(topic, e); }
  }
}

export function setSnapshot(snap) {
  state.snap = snap;
  emit("snapshot", snap);
  emit("any");
}

// Only the parts of the snapshot that changed since the last one.  The
// parts left alone keep their objects, so a panel can tell they didn't move.
export function applySnapDiff(diff) {
  if (!state.snap) return;
  const next = { ...state.snap, ...(diff.set || {}) };
  for (const k of diff.del || []) delete next[k];
  setSnapshot(next);
}

export function setLite(lite) {
  const before = state.snap ? JSON.stringify(state.snap.selected || []) : "";
  state.lite = lite;
  if (state.snap) {
    // keep the snapshot's fast fields current so a panel can read one place
    for (const k of ["master", "blackout", "selected", "live", "dry_run", "output",
      "programmer", "undo", "fx", "lock", "quick_active"]) {
      if (k in lite) state.snap[k] = lite[k];
    }
    if (Array.isArray(lite.playbacks) && Array.isArray(state.snap.playbacks)) {
      lite.playbacks.forEach((p) => {
        const full = state.snap.playbacks.find((x) => x.n === p.n);
        if (full) Object.assign(full, p);
      });
    }
  }
  emit("lite", lite);
  if (state.snap && JSON.stringify(state.snap.selected || []) !== before) emit("selection", state.snap.selected);
  emit("any");
}

export function setLooks(rows) {
  const next = {};
  for (const r of rows || []) next[r.n] = r;
  state.looks = next;
  emit("looks", next);
}

// ------------------------------------------------------------ derived
export const patch = () => (state.snap && state.snap.patch) || [];
export const selected = () => (state.snap && state.snap.selected) || [];
export const head = (n) => patch().find((h) => h.head_no === n);

export function selectionHeads() {
  const set = new Set(selected());
  return patch().filter((h) => set.has(h.head_no));
}

export function outputState() {
  const s = state.snap;
  if (!s) return "blind";
  if (s.dry_run) return "blind";
  return s.live ? "live" : "armed";
}
