// The Palettes tab: what the show has saved, in four fixed pools - the
// looks (everything at once) across the top, then the colours, the
// positions and the beams.  A tile is numbered and named (a colour shows
// its colour, a beam its gobo); tap one and the selection takes it; it is
// lit while the selected lights hold it; right-click to update, rename or
// delete.  Nothing to arrange: each pool has its place.
import { state, selected, patch } from "./store.js";
import { run } from "./actions.js";
import { $, h, menu, toast, promptBox, confirmBox } from "./ui.js";
import { goboIcon } from "./goboicon.js";

const POOLS = [["colour", "Colours", "C"], ["position", "Positions", "P"], ["beam", "Beams", "B"]];
const DEFAULTS_LABEL = { colour: "Make the usual colours", position: "Make the usual positions", beam: "Make Narrow / Mid / Wide" };

const snap = () => state.snap || {};
const progVals = () => ((snap().programmer || {}).values) || {};

// Can the selected lights take this palette?  A per-light palette only on
// the lights it was recorded on; any palette only on lights that have its
// channels (no gobo wheel: no gobo palette).  null: nothing selected.
export function paletteFits(p) {
  const sel = selected();
  if (!sel.length) return null;
  const roles = (o) => Object.keys(o || {}).map((r) => r.replace(/@\d+$/, "").replace(/_fine$/, ""));
  for (const n of sel) {
    const head = patch().find((x) => x.head_no === n);
    if (!head) continue;
    const own = p.per ? p.per[String(n)] : p.values;
    if (!own) continue;
    const map = head.map || [];
    if (roles(own).some((r) => map.includes(r)) || (own.hex && map.some((r) => ["red", "cyan", "wheel", "colour_macro"].includes(r)))) return true;
  }
  return false;
}

// a palette is lit when every selected light that it has values for holds them
export function paletteOn(kind, p) {
  const sel = selected();
  if (!sel.length) return false;
  const vals = progVals();
  let any = false;
  for (const n of sel) {
    const head = patch().find((x) => x.head_no === n);
    if (!head) continue;
    const own = (p.per || {})[String(n)] || p.values || {};
    const row = Object.entries(own).filter(([r]) => (head.map || []).includes(r));
    if (!row.length) continue;
    any = true;
    const pv = vals[String(n)] || {};
    if (!row.every(([r, v]) => pv[r] === v)) return false;
  }
  return any;
}

// a beam palette with a gobo in it shows that gobo: its picture on the
// first light it was recorded on that has gobo pictures
function goboOf(p) {
  const rows = Object.entries(p.per || {}).map(([n, v]) => [+n, v]);
  if (!rows.length) rows.push(...patch().map((x) => [x.head_no, p.values || {}]));
  for (const [n, v] of rows) {
    if (!v || !("gobo" in v)) continue;
    const hd = patch().find((x) => x.head_no === n);
    if (!hd || !(hd.gobos || []).length) continue;
    const g = hd.gobos.find((r) => v.gobo >= r[0] && v.gobo <= r[1]);
    // the light's gobo list names only the gobos with a picture: a value
    // on none of them is the open slot (or a spin range) - a clear disc
    return g ? g[2] : "";
  }
  return null;
}

/** What a palette tile shows of it: a colour's colour, a beam's gobo. */
export function paletteSwatch(kind, p) {
  if (kind === "beam") {
    const ref = goboOf(p);
    return ref === null ? null : goboIcon(ref);
  }
  if (kind !== "colour") return null;
  const v = p.values || {};
  const hex = p.hex || (("red" in v || "green" in v || "blue" in v)
    ? "#" + ["red", "green", "blue"].map((r) => Math.max(0, Math.min(255, v[r] || 0)).toString(16).padStart(2, "0")).join("") : null);
  return hex ? h("i.pool-sw", { style: { background: hex } }) : null;
}

// the usual palettes, offered only when a light here can do them (no
// "Narrow / Mid / Wide" for a rig with no zoom) - its x puts the offer away
const OFFER_ATTRS = { colour: ["red", "wheel", "cyan", "colour_macro"], beam: ["zoom"], position: ["pan"] };
const canMake = (kind) => patch().some((x) => (x.map || []).some((r) => (OFFER_ATTRS[kind] || []).includes(r))
  && (kind !== "position" || (x.map || []).includes("tilt")));
const noOffer = () => { try { return JSON.parse(localStorage.getItem("haze.dvNoOffer") || "[]"); } catch (e) { return []; } };
const setNoOffer = (v) => { try { localStorage.setItem("haze.dvNoOffer", JSON.stringify(v)); } catch (e) { /* private window */ } };

function paletteMenu(anchor, kind, p) {
  menu(anchor, [
    { label: "Update from the programmer", hint: "the cues that use it follow", run: () => {
      if (!selected().length) { toast("Select the lights and set them first"); return; }
      run("record_palette", { kind, palette: p.n, name: p.name }, { toast: true });
    } },
    { label: "Rename…", run: async () => { const name = await promptBox("Rename palette", "Name", p.name, { ok: "Rename" }); if (name) run("palette_edit", { kind, palette: p.n, name }, { toast: true }); } },
    "-",
    { label: "Delete", danger: true, hint: "cues keep their values", run: async () => {
      if (await confirmBox("Delete palette", `Delete “${p.name}”?`, { ok: "Delete", danger: true })) run("palette_edit", { kind, palette: p.n, delete: true }, { toast: true });
    } },
  ]);
}

const record = (kind) => async () => {
  if (!selected().length) { toast("Select lights and set them first"); return; }
  const name = await promptBox(`Save a ${kind} palette`, "Name", "", { ok: "Save" });
  if (name) run("record_palette", { kind, name }, { toast: true });
};

function poolTiles(kind, letter) {
  const pals = (snap().palettes || {})[kind] || [];
  const tiles = pals.map((p) => {
    const fits = paletteFits(p);
    const per = p.per ? " (each light keeps its own)" : "";
    const tile = h("button.pool-tile" + (paletteOn(kind, p) ? ".on" : "") + (fits === false ? ".na" : ""), {
      type: "button", dataset: { kind },
      title: fits === false ? `${p.name}${per}: the selected lights can't take it (${p.per ? "it was recorded on other lights" : "they don't have its channels"}) · right-click for more`
        : `${p.name}${per}: tap to put it on the selected lights · right-click for more`,
      onclick: () => {
        if (!selected().length) { toast("Select lights first"); return; }
        if (fits === false) { toast(`${p.name} isn't for the selected lights`); return; }
        run("include_palette", { kind, palette: p.n });
      },
      oncontextmenu: (e) => { e.preventDefault(); paletteMenu(e.currentTarget, kind, p); },
    }, paletteSwatch(kind, p), h("small.pool-n", `${letter}${p.n}`), h("b", p.name));
    return tile;
  });
  tiles.push(h("button.pool-tile.pool-add", { type: "button", title: `Save the selection's ${kind} as a palette`, "aria-label": `Save a ${kind} palette`,
    onclick: record(kind) }, "+ Save"));
  if (!pals.length && canMake(kind) && !noOffer().includes(kind)) {
    const t = h("button.pool-tile.pool-add.pool-offer", { type: "button", title: "For this rig, each light its own values",
      onclick: () => run("palette_defaults", { kind }, { toast: true }) }, DEFAULTS_LABEL[kind]);
    t.append(h("span.pool-x", { role: "button", title: "Don't offer this here", "aria-label": "Don't offer this",
      onclick: (e) => { e.stopPropagation(); setNoOffer([...noOffer(), kind]); t.remove(); } }, "×"));
    tiles.push(t);
  }
  return tiles;
}

let lastKey = "";
/** Draw the colour, position and beam pools (the looks pool is the
 *  programmer's: renderLooks). */
export function renderPools(force = false) {
  const box = $("#pools");
  if (!box || box.closest("[hidden]")) return;
  const pals = snap().palettes || {};
  const key = JSON.stringify([pals, selected(), progVals(), noOffer(), patch().length]);
  if (!force && key === lastKey) return;            // the same: tiles stay put under the pointer
  lastKey = key;
  for (const [kind, , letter] of POOLS) {
    const tiles = $(`#pool-${kind}`);
    if (tiles) tiles.replaceChildren(...poolTiles(kind, letter));
    const n = $(`#pool-${kind}-n`);
    if (n) n.textContent = String((pals[kind] || []).length);
  }
}
