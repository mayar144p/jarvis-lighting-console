// The rig tools, each where it is wanted rather than on a tab of its own:
//  * Fan - the value bar's Fan… key: one value spread across the selection,
//    from the first light to the last (or the other way, from the centre
//    out, into the centre, at random).
//  * Limits and orientation - each light's menu and the side panel's tools:
//    how far an attribute may go, which way pan and tilt run, line up.
//  * Arrange - the side panel's tools: line the selected lights up, spread
//    them evenly or mirror them, across the stage, front to back or in
//    height.
//  * The selection's shortcuts - the Lights ⋯: the same type as the
//    first, the movers, the lights with gobos, one light at a time.
import { run } from "./actions.js";
import { patch, selected } from "./store.js";
import { h, modal, toast } from "./ui.js";

// the attributes these lights have (not the fine halves or unused channels)
function rolesOf(heads) {
  const want = new Set(heads);
  const roles = new Set();
  for (const hd of patch()) {
    if (!want.has(hd.head_no)) continue;
    for (const r of hd.map || []) if (!["raw", "unused"].includes(r) && !r.endsWith("_fine")) roles.add(r);
  }
  return [...roles].sort();
}
const field = (label, el) => h("label.field", h("span", label), el);
function pick(id, roles, cur) {
  const s = h("select.select#" + id, ...roles.map((r) => h("option", { value: r }, r)));
  if (cur && roles.includes(cur)) s.value = cur;
  return s;
}
const choice = (id, list) => h("select.select#" + id, ...list.map(([v, l]) => h("option", { value: v }, l)));
const LEVELS = new Set(["dimmer", "intensity"]);

/** Fan: spread one value across the selected lights. */
export function openFan(attr = "") {
  const heads = selected();
  if (heads.length < 2) { toast("Select two lights or more to fan a value across them"); return; }
  const roles = rolesOf(heads);
  if (!roles.length) { toast("The selected lights have nothing to fan"); return; }
  const what = pick("fan-attr", roles, attr || (roles.includes("dimmer") ? "dimmer" : roles[0]));
  const lo = h("input.num#fan-lo", { type: "number", placeholder: "first" });
  const hi = h("input.num#fan-hi", { type: "number", placeholder: "last" });
  const range = h("small.muted");
  const showRange = () => { range.textContent = LEVELS.has(what.value) ? "a level: 0-100" : "a channel: 0-255"; };
  what.addEventListener("change", showRange);
  showRange();
  const how = choice("fan-mode", [["normal", "First light to last"], ["reverse", "Last light to first"], ["centre_out", "From the centre out"],
    ["into_centre", "Into the centre"], ["random", "At random"]]);
  const by = choice("fan-by", [["order", "In the order you picked them"], ["position", "Left to right on the stage"]]);
  let close = () => {};
  const go = async () => {
    if (lo.value === "" || hi.value === "") { toast("Give the first and the last light's value"); return; }
    const r = await run("fan", { attribute: what.value, from_value: +lo.value, to_value: +hi.value, mode: how.value, by: by.value }, { toast: true });
    if (r.ok) close();
  };
  close = modal({
    title: "Fan a value",
    body: h("div.rig-form",
      h("p.muted.small", `One value spread across the ${heads.length} selected lights - a warm-to-cool sweep, movers fanned out.`),
      field("Attribute", what),
      h("div.rig-pair", field("First light", lo), field("Last light", hi), range),
      h("div.rig-pair", field("Which way", how), field("Their order", by))),
    foot: [h("span.grow"), h("button.btn", { onclick: () => close() }, "Cancel"), h("button.btn.primary", { onclick: go }, "Fan")],
  });
}

/** Limits and orientation of some lights (the selection by default). */
export function openLimits(heads = selected()) {
  if (!heads.length) { toast("Select the lights first"); return; }
  const roles = rolesOf(heads);
  const moving = roles.includes("pan") || roles.includes("tilt");
  const role = pick("lim-role", roles, moving ? "tilt" : roles.includes("dimmer") ? "dimmer" : roles[0]);
  const lo = h("input.num#lim-lo", { type: "number", placeholder: "low" });
  const hi = h("input.num#lim-hi", { type: "number", placeholder: "high" });
  const orient = (k, label, title) => h("button.btn", { title, onclick: () => run("set_orient", k === "clear" ? { heads, clear: true } : { heads, [k]: true }, { toast: true }) }, label);
  let close = () => {};
  close = modal({
    title: `Limits and orientation · ${heads.length} light${heads.length === 1 ? "" : "s"}`,
    body: h("div.rig-form",
      h("h3", "Limits"),
      h("p.muted.small", "How far an attribute may go on these lights, wherever the value comes from - a cue, a button, an effect (a level 0-100, a channel 0-255)."),
      h("div.rig-pair", field("Attribute", role), field("Lowest", lo), field("Highest", hi)),
      h("div.row-btns",
        h("button.btn", { onclick: () => run("set_limits", { heads, role: role.value, low: +(lo.value || 0), high: +(hi.value || 255) }, { toast: true }) }, "Set the limit"),
        h("button.btn.ghost", { onclick: () => run("clear_limits", { heads, role: role.value }, { toast: true }) }, "Clear it")),
      moving ? h("h3", "Orientation") : null,
      moving ? h("p.muted.small", "Hung upside down or turned round? Make pan and tilt run the way the desk expects.") : null,
      moving ? h("div.row-btns",
        orient("invert_pan", "Invert pan", "It pans the other way"), orient("invert_tilt", "Invert tilt", "It tilts the other way"),
        orient("swap", "Swap pan / tilt", "Its pan and tilt are swapped"), h("span.grow"), orient("clear", "Back to normal", "No inverting or swapping")) : null,
      moving ? h("div.row-btns", h("button.btn#lineup-btn", { title: "Point the real light at marks on the floor: the desk learns where it really points, so aims and the 3D match it",
        onclick: () => { close(); import("./lineup.js").then((m) => m.openLineUp(heads)); } }, "Line up with the real light…")) : null),
    foot: [h("span.grow"), h("button.btn", { onclick: () => close() }, "Done")],
  });
}

/** Arrange the selected lights on the stage (their places in the 3D). */
export function openArrange() {
  const heads = selected();
  if (heads.length < 2) { toast("Select two lights or more to arrange them"); return; }
  const axis = choice("arr-axis", [["x", "Across the stage (left - right)"], ["z", "Front to back (up - downstage)"], ["y", "In height"]]);
  const act = (name, label, title) => h("button.btn", { title, onclick: () => run(name, { heads, axis: axis.value }, { toast: true }) }, label);
  let close = () => {};
  close = modal({
    title: `Arrange ${heads.length} lights`,
    body: h("div.rig-form",
      h("p.muted.small", "Where the selected lights sit in the room - the 3D and the plot follow; Undo puts them back."),
      field("Which way", axis),
      h("div.row-btns",
        act("align", "Line them up", "All at the same place that way (the first light's)"),
        act("distribute", "Spread them evenly", "Even gaps between the first and the last"),
        act("mirror", "Mirror them", "Swap them round the middle"))),
    foot: [h("span.grow"), h("button.btn", { onclick: () => close() }, "Done")],
  });
}

/** Select by kind, one light at a time (the Lights ⋯). */
export function selectItems() {
  const sel = selected();
  return [
    { label: "The same type as the first", hint: sel.length ? "" : "select one first", disabled: !sel.length, run: () => run("select_similar", { head: sel[0] }) },
    { label: "Every moving light", run: () => run("select_query", { role: "pan" }, { toast: true }) },
    { label: "Every light with gobos", run: () => run("select_query", { role: "gobo" }, { toast: true }) },
    "-",
    { label: "One at a time: the next light", disabled: sel.length < 2, run: () => run("select_step", { step: "next" }, { toast: true }) },
    { label: "One at a time: the light before", disabled: sel.length < 2, run: () => run("select_step", { step: "prev" }, { toast: true }) },
    { label: "All of them again", run: () => run("select_step", { step: "all" }, { toast: true }) },
    "-",
  ];
}
