// Which lights show their real 3D (their own GDTF, or the maker's body from
// GDTF Share) and which a drawn placeholder.  One ask per change of the
// models in the patch, shared by the 3D view and the fixture list, so both
// say the same thing - and the desk window knows without a 3D of its own.
import { get } from "./api.js";
import { state, emit, patch } from "./store.js";

let sig = null;
let defs = [];
let asking = null;

/** The definitions from /api/console/models (re-asked when the models change). */
export function modelDefs(force = false) {
  const p = patch();
  const now = p.map((h) => h.manufacturer + "/" + h.model + "/" + h.mode).sort().join("|");
  if (!force && now === sig) return asking || Promise.resolve(defs);
  sig = now;
  const mine = asking = get("/api/console/models").then((d) => {
    if (mine !== asking) return defs;
    defs = (d && d.definitions) || [];
    const real = new Set();
    for (const def of defs) if (def.ok) for (const n of def.heads || []) real.add(n);
    state.drawn3d = new Set(p.map((h) => h.head_no).filter((n) => !real.has(n)));
    emit("models3d", state.drawn3d);
    return defs;
  }).catch(() => defs).finally(() => { if (mine === asking) asking = null; });
  return mine;
}

/** True when this light shows a drawn placeholder (null: not known yet). */
export const isDrawn = (head) => (state.drawn3d ? state.drawn3d.has(head) : null);
