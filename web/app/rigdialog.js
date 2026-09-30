// Arrange -> Rigging…: the rigging library (a straight run, a corner, a
// frame, a circle, a goal post, a pole or a stand, made of a real piece at
// its real size, hung at a trim height) and the rigging report (each piece,
// the lights on it, the pick-up points and the load on each, a parts list).
import { act } from "./api.js";
import { run } from "./actions.js";
import { state } from "./store.js";
import { h, modal, toast } from "./ui.js";

const SHAPES = [
  ["straight", "Straight", "─", ["length"]],
  ["corner", "Corner", "┐", ["width", "depth"]],
  ["frame", "Frame", "▭", ["width", "depth"]],
  ["circle", "Circle", "◯", ["diameter"]],
  ["goalpost", "Goal post", "╥", ["width", "height"]],
  ["pole", "Pole", "│", ["height"]],
  ["stand", "Stand", "┴", ["height"]],
];
const LABEL = { length: "Length m", width: "Width m", depth: "Depth m", diameter: "Diameter m", height: "Height m" };
const DEF = { length: 4, width: 6, depth: 4, diameter: 4, height: 3 };

function room() {
  const r = ((state.snap && state.snap.venue) || {}).room || {};
  return { h: +r.height || 6, cx: +r.cx || 0, back: r.back ?? -1, depth: +r.depth || 10 };
}

export async function openRigDialog(tab = "add") {
  const lib = await act("rig_pieces", {});
  if (!lib.ok) { toast(lib.error || "No rigging library", "bad"); return; }
  const R = room();
  const pick = { shape: "straight", piece: "box30", ...DEF, trim: +(R.h - 0.8).toFixed(1), x: R.cx, z: +(R.back + R.depth * 0.35).toFixed(1), rot: 0 };
  const body = h("div.rigdlg");
  let close = null;
  const tabs = h("div.seg", ...[["add", "Add rigging"], ["report", "Rigging report"]].map(([k, l]) =>
    h("button" + (k === tab ? ".on" : ""), { onclick: () => { tab = k; draw(); } }, l)));

  const numIn = (key, attrs = {}) => {
    const el = h("input", { type: "number", step: 0.5, value: pick[key], ...attrs });
    el.addEventListener("change", () => { pick[key] = +el.value; });
    return h("label.field", h("span", LABEL[key] || key), el);
  };

  const drawAdd = () => {
    const shape = SHAPES.find((s) => s[0] === pick.shape);
    const pieces = lib.pieces.filter((p) => ["pole", "stand"].includes(pick.shape)
      ? p.kind === (pick.shape === "pole" ? "tower" : "stand")
      : ["truss", "pipe"].includes(p.kind));
    if (!pieces.some((p) => p.id === pick.piece)) pick.piece = pieces[0].id;
    const sel = h("select.select", ...pieces.map((p) => h("option", { value: p.id, selected: p.id === pick.piece },
      `${p.name} · ${p.kg_m ? p.kg_m + " kg/m" : p.kg + " kg"}`)));
    sel.addEventListener("change", () => { pick.piece = sel.value; });
    const hung = !["pole", "stand", "goalpost"].includes(pick.shape);
    return h("div",
      h("div.rig-shapes", ...SHAPES.map(([k, l, icon]) => h("button.rig-shape" + (k === pick.shape ? ".on" : ""), {
        onclick: () => { pick.shape = k; draw(); },
      }, h("b", icon), h("span", l)))),
      h("label.field", h("span", "Piece"), sel),
      h("div.form-grid", ...shape[3].map((k) => numIn(k, { min: 0.5 })),
        hung ? numIn("trim", { min: 0.3, max: R.h - 0.1, step: 0.1 }) : null),
      h("div.form-grid", numIn("x"), numIn("z"), numIn("rot", { step: 15 })),
      h("p.muted.small", hung
        ? `Trim: the height of its underside (the room is ${R.h} m). X / Z: where its middle goes. Ends near another piece's end join when you drag it.`
        : "It stands on the floor at X / Z."),
      h("div.row-btns", h("button.btn.primary", {
        onclick: async () => {
          const p = { preset: pick.shape, piece: pick.piece, x: pick.x, z: pick.z, rot: pick.rot };
          for (const k of shape[3]) p[k] = pick[k];
          if (hung) p.trim = pick.trim;
          const r = await run("rig_add", p, { toast: true });
          if (r.ok) close();
        },
      }, `Add ${shape[1].toLowerCase()}`)));
  };

  const drawReport = async () => {
    const box = h("div", h("p.muted.small", "Working it out…"));
    const r = await act("rig_report", { csv: true });
    if (!r.ok) { box.replaceChildren(h("p.bad", r.error || "No report")); return box; }
    const rep = r.report;
    const rows = rep.rigs.map((x) => h("tr" + (x.warnings.length ? ".warn" : ""),
      h("td", h("b", x.name), x.pieces > 1 ? h("small.muted", ` ${x.pieces} pieces`) : null,
        x.warnings.length ? h("div.small.warn-text", "⚠ " + x.warnings.join(" · ")) : null),
      h("td.mono", x.length.toFixed(1)), h("td.mono", x.trim == null ? "–" : x.trim.toFixed(2)),
      h("td.mono", x.self_kg), h("td.mono", { title: x.lights.map((l) => `#${l.head} ${l.kg} kg${l.guessed ? " (typical)" : ""}`).join("\n") }, `${x.lights.length} · ${x.light_kg}`),
      h("td.mono", h("b", x.total_kg)),
      h("td.mono", x.ground ? "ground" : `${x.points} × ${x.per_point_kg}`)));
    const dl = h("button.btn.small", {
      onclick: () => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([r.csv], { type: "text/csv" }));
        a.download = "rigging-report.csv";
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      },
    }, "Download CSV");
    box.replaceChildren(
      h("div.rig-totals", h("span", h("b", rep.total_kg + " kg"), " in the air and on stands"),
        h("span", h("b", rep.points), " pick-up points"), rep.total_watts ? h("span", h("b", rep.total_watts + " W"), " of lights") : null, dl),
      h("table.chan-table.rig-report", h("thead", h("tr", ...["Piece", "Length m", "Trim m", "Own kg", "Lights · kg", "Total kg", "Points × kg"].map((t) => h("th", t)))),
        h("tbody", ...(rows.length ? rows : [h("tr", h("td", { colspan: 7, class: "muted" }, "No rigging yet."))]))),
      rep.parts.length ? h("div", h("h3", "Parts list"), h("ul.rig-parts", ...rep.parts.map((p) =>
        h("li", h("b", `${p.count} ×`), ` ${p.name}`, p.length ? ` · ${typeof p.length === "number" ? p.length + " m" : p.length}` : "")))) : null,
      h("p.muted.small", "Weights: typical for each kind of truss, the lights' own from their library files (typical for the kind of light where a file doesn't say). A guide for planning - your rigger signs off the real loads."));
    return box;
  };

  const draw = async () => {
    for (const b of tabs.children) b.classList.toggle("on", b.textContent === (tab === "add" ? "Add rigging" : "Rigging report"));
    body.replaceChildren(tabs, tab === "add" ? drawAdd() : await drawReport());
  };
  close = modal({ title: "Rigging", body, wide: true, foot: [h("button.btn", { onclick: () => close() }, "Close")] });
  draw();
}
