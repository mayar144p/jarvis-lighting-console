// Set up's screen: the patch on the left - every light on one line (its
// number, kind, name, maker, mode, address, channels and groups) with each
// universe drawn under it - and on the right what is still to do before
// the show, with the selected light's name, mode, address and which way
// it is hung under that.
import { h, menu, toast, confirmBox } from "./ui.js";
import { state, on, selected } from "./store.js";
import { run } from "./actions.js";
import { get } from "./api.js";
import { typeIcon, rowMenu, atPointer, shortName } from "./fixtures.js";
import { groupColour } from "./groupcolour.js";

const snap = () => state.snap || {};
const patch = () => snap().patch || [];
const sel = () => new Set(selected());
const addr = (hd) => `${hd.universe}.${String(hd.address).padStart(3, "0")}`;
const lastAddr = (hd) => `${hd.universe}.${String(hd.address + Math.max(1, hd.channels || 1) - 1).padStart(3, "0")}`;
const lazy = (mod, fn, ...args) => () => import(mod).then((m) => m[fn](...args));
const maker = (hd) => { const b = hd.body || {}; return b.brand_name && b.brand_name !== "Generic" ? b.brand_name : hd.manufacturer || ""; };
const kindOf = (hd) => (hd.body && hd.body.label) || "";
const moving = (hd) => (hd.map || []).includes("pan") || (hd.map || []).includes("tilt");
const isFx = (hd) => (hd.map || []).some((r) => r.startsWith("laser_") || r.startsWith("fx_") || r === "fog") || (hd.body || {}).class === "sfx";

/** Draw Set up into the hub's body (again whenever the show changes). */
export function paintSetupScreen(box, steps) {
  let grid = box.querySelector(":scope > .su-grid");
  if (!grid) {
    grid = h("div.su-grid",
      h("section.su-patch", { "aria-label": "Patch" },
        h("div.su-head",
          h("h2", "Patch"),
          h("button.btn.primary", { type: "button", onclick: lazy("./dialogs.js", "openAddDialog") }, "+ Add lights"),
          h("button.btn", { type: "button", "aria-haspopup": "menu", onclick: (e) => importMenu(e.currentTarget) }, "Import ▾"),
          h("button.btn", { type: "button", title: "Every light from 1.001 on, no gaps or overlaps (Undo takes it back)", onclick: autoAddress }, "Auto-address"),
          h("button.btn", { type: "button", title: "The DMX nodes on the network", onclick: lazy("./fixtures.js", "scanRig") }, "Find nodes"),
          h("span.grow"),
          h("button.btn.ghost.icon", { type: "button", "aria-label": "More for the patch", title: "DMX map, ask the lights (RDM), venue check-in, export the patch", onclick: (e) => patchMenu(e.currentTarget) }, "⋯")),
        h("div.su-table-wrap", h("table.su-table",
          h("thead", h("tr", ...["No.", "Light", "Maker", "Kind", "Mode", "Address", "Channels", "Groups"].map((t, i) =>
            h("th" + (i === 0 ? ".c-no" : i === 5 ? ".c-addr" : i === 6 ? ".c-ch" : ""), t)))),
          h("tbody#su-rows"))),
        // (beside the table, not in it: the table hides while there are no lights)
        h("div.su-empty", h("b", "No lights yet"), h("span.muted", "Add the lights that are hung, or import a plot or a patch.")),
        h("div.su-unis#su-unis")),
      h("aside.su-side",
        h("section.su-check#su-check", { "aria-label": "Before the show" }),
        h("section.su-insp#su-insp", { "aria-label": "The selected light" })));
    box.replaceChildren(grid);
    rowsKey = unisKey = inspKey = checkKey = "";
    wireRows(grid.querySelector("#su-rows"));
  }
  paintRows(grid);
  paintUniverses(grid.querySelector("#su-unis"));
  paintChecklist(grid.querySelector("#su-check"), steps);
  paintInspector(grid.querySelector("#su-insp"));
}

// ------------------------------------------------------------- the patch
function importMenu(anchor) {
  menu(anchor, [
    { label: "A plot (MVR)…", hint: "from Capture, Vectorworks, grandMA…", run: lazy("./dialogs.js", "importMvr") },
    { label: "A patch (CSV)…", hint: "number, model, mode, address", run: lazy("./dialogs.js", "openCsvImport") },
  ]);
}

function patchMenu(anchor) {
  menu(anchor, [
    { label: "DMX map…", hint: "who uses which channels", run: lazy("./fixtures.js", "openDmxMap") },
    { label: "Ask the lights (RDM)…", hint: "model, mode and address from each light", run: lazy("./fixtures.js", "openRdm") },
    { label: "Venue check-in…", hint: "the lights found here against the show", run: lazy("./checkin.js", "openCheckin") },
    "-",
    { label: "Export the patch (CSV)", run: lazy("./fixtures.js", "exportCsv") },
  ]);
}

async function autoAddress() {
  if (await confirmBox("Auto-address", "Re-address every light from 1.001 with no gaps or overlaps? Undo takes it back.", { ok: "Re-address" })) run("auto_patch", {}, { toast: true });
}

function groupsOf(n) {
  return (snap().groups || []).map((g, i) => [g, i]).filter(([g]) => (g.heads || []).includes(n) && !/^auto\s*·/i.test(g.name || ""));
}

let rowsKey = "";
function paintRows(grid) {
  const list = patch();
  const groups = snap().groups || [];
  const key = JSON.stringify([list.map((x) => [x.head_no, x.name, x.model, x.mode, x.universe, x.address, x.channels, kindOf(x), maker(x)]),
    groups.map((g) => [g.n, g.name, g.colour, g.heads])]);
  grid.classList.toggle("is-empty", !list.length);
  if (key !== rowsKey) {
    rowsKey = key;
    grid.querySelector("#su-rows").replaceChildren(...list.map((hd) => {
      const gs = groupsOf(hd.head_no);
      return h("tr", { dataset: { head: hd.head_no }, title: [hd.model, maker(hd), hd.mode].filter(Boolean).join(" · ") },
        h("td.c-no", String(hd.head_no)),
        h("td.c-name", h("div.su-name", typeIcon(hd.body || {}, hd.head_no), h("b", shortName(hd)))),
        h("td.c-maker", maker(hd)),
        h("td.c-kind", kindOf(hd)),
        h("td.c-mode", hd.mode || ""),
        h("td.c-addr", addr(hd)),
        h("td.c-ch", String(hd.channels || "")),
        h("td.c-groups", { title: gs.map(([g]) => g.name).join(", ") },
          ...gs.slice(0, 2).map(([g, i]) => h("span.su-grp", h("i", { style: `--c:${groupColour(g, i)}` }), g.name)),
          gs.length > 2 ? h("span.su-more", "…") : null));
    }));
  }
  markSelected();
}

function markSelected() {
  const s = sel();
  for (const tr of document.querySelectorAll("#su-rows tr")) tr.classList.toggle("sel", s.has(+tr.dataset.head));
}

// a click picks a light (Ctrl adds or takes it out, Shift a run of them);
// a right-click is the light's menu
let anchorHead = null;
function wireRows(tbody) {
  tbody.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-head]");
    if (!tr) return;
    const n = +tr.dataset.head;
    const cur = sel();
    if (e.shiftKey && anchorHead !== null) {
      const nums = patch().map((x) => x.head_no);
      const a = nums.indexOf(anchorHead), b = nums.indexOf(n);
      if (a >= 0 && b >= 0) { pick(nums.slice(Math.min(a, b), Math.max(a, b) + 1), e.ctrlKey || e.metaKey); return; }
    }
    anchorHead = n;
    if (e.ctrlKey || e.metaKey) {
      if (cur.has(n)) { cur.delete(n); pick([...cur]); } else pick([n], true);
    } else pick([n]);
  });
  tbody.addEventListener("contextmenu", (e) => {
    const tr = e.target.closest("tr[data-head]");
    if (!tr) return;
    e.preventDefault();
    rowMenu(atPointer(e), +tr.dataset.head);
  });
}
function pick(heads, add = false) {
  const now = add ? [...new Set([...selected(), ...heads])] : heads;
  run("select_heads", { heads: now });
}

// each universe: its channels in use, a block per light (the selected
// ones lit, lasers and effects in amber, a clash in red)
let unisKey = "";
function paintUniverses(box) {
  const list = patch();
  const s = sel();
  const clashes = snap().clashes || [];
  const key = JSON.stringify([list.map((x) => [x.head_no, x.universe, x.address, x.channels]), [...s], clashes]);
  if (key === unisKey) return;
  unisKey = key;
  const unis = [...new Set(list.map((x) => x.universe || 1))].sort((a, b) => a - b);
  const clashHeads = new Set(clashes.flatMap((c) => [c.a, c.b, c.head, ...(c.heads || [])].filter((x) => x !== undefined)));
  // several universes: slimmer strips, the key once at the top, the scale under the last
  box.classList.toggle("many", unis.length > 1);
  box.replaceChildren(...unis.map((u, ui) => {
    const on = list.filter((x) => (x.universe || 1) === u);
    const used = on.reduce((n, x) => n + Math.max(1, x.channels || 1), 0);
    const nClash = clashes.filter((c) => (c.universe || u) === u).length;
    const blocks = on.map((x) => h("i" + (s.has(x.head_no) ? ".sel" : "") + (clashHeads.has(x.head_no) ? ".clash" : isFx(x) ? ".fx" : ""), {
      style: `left:${((x.address - 1) / 512) * 100}%;width:${(Math.max(1, x.channels || 1) / 512) * 100}%`,
      title: `#${x.head_no} ${x.name || x.model}: ${addr(x)}-${lastAddr(x).split(".")[1]}`,
      onclick: () => pick([x.head_no]) }));
    return h("div.su-uni",
      h("div.su-uni-head", h("b", `Universe ${u}`),
        h("span", `${used} of 512 channels used, ${nClash ? `${nClash} clash${nClash === 1 ? "" : "es"}` : "no clashes"}`),
        h("span.grow"),
        ui === 0 ? h("span.su-key", h("i.sel"), "selected") : null, ui === 0 ? h("span.su-key", h("i.fx"), "lasers and effects") : null,
        h("button.btn.ghost.small", { type: "button", title: `Universe ${u}: every channel, light by light, and any clash`,
          onclick: () => import("./fixtures.js").then((m) => m.openDmxMap(u)) }, "DMX map…")),
      h("div.su-uni-bar", ...blocks),
      ui === unis.length - 1 ? h("div.su-uni-axis", ...["1", "128", "256", "384", "512"].map((t) => h("span", t))) : null);
  }));
}

// ------------------------------------------------------- before the show
// steps: [[group, [{title, status, ok, buttons: [[label, fn]...], optional}]]]
let checkKey = "";
function paintChecklist(box, steps) {
  // (drawn again only when a step changes: its keys stay under the pointer)
  const key = JSON.stringify(steps.map(([g, list]) => [g, list.map((st) => [st.title, st.status, st.ok, st.optional, st.buttons.filter(Boolean).map(([l]) => l)])]));
  if (key === checkKey && box.firstChild) return;
  checkKey = key;
  const all = steps.flatMap(([, list]) => list);
  const judged = all.filter((st) => st.ok !== null && !st.optional);
  const done = judged.filter((st) => st.ok).length;
  const next = all.find((st) => st.ok === false);
  const hidden = checkHidden();
  box.classList.toggle("collapsed", hidden);
  // the head folds the steps away (and back); folded, it still says how far and what's next
  box.replaceChildren(
    h("button.su-check-head", { type: "button", "aria-expanded": String(!hidden), "aria-controls": "su-steps",
      title: hidden ? "Show the steps" : "Hide the steps", onclick: () => { setCheckHidden(!checkHidden()); checkKey = ""; paintChecklist(box, steps); } },
      h("i.su-chev", { "aria-hidden": "true" }),
      h("span.su-check-title", h("h2", "Before the show"), hidden && next ? h("small", `Next: ${next.title.toLowerCase()}`) : null),
      h("span.su-check-count", `${done} of ${judged.length} done`)),
    h("div.su-steps#su-steps", { hidden }, ...steps.map(([name, list]) => [
      h("h3.su-step-group", name),
      ...list.map((st) => stepRow(st, st === next)),
    ]).flat()));
}
const checkHidden = () => { try { return localStorage.getItem("haze.setup.checkHidden") === "1"; } catch (e) { return false; } };
const setCheckHidden = (v) => { try { localStorage.setItem("haze.setup.checkHidden", v ? "1" : "0"); } catch (e) { /* private window */ } };

function stepRow(st, isNext) {
  const [main, ...rest] = st.buttons.filter(Boolean);
  const mark = st.ok === true ? h("i.su-mark.ok", { "aria-label": "done" }, "✓") : st.ok === false ? h("i.su-mark.todo", { "aria-label": "to do" }, "!")
    : h("i.su-mark", { "aria-hidden": "true" });
  return h("div.su-step" + (isNext ? ".next" : "") + (st.optional ? ".opt" : ""),
    mark,
    h("div.su-step-text", h("b", st.title), h("span", st.status)),
    main ? h("button.btn.small" + (isNext ? ".primary" : ""), { type: "button", onclick: main[1] }, main[0]) : null,
    rest.length ? h("button.btn.ghost.small.icon", { type: "button", "aria-label": `${st.title}: more`, title: rest.map(([l]) => l).join(" · "),
      onclick: (e) => menu(e.currentTarget, rest.map(([label, fn]) => ({ label, run: fn }))) }, "⋯") : null);
}

// ------------------------------------------------------ the light picked
let inspKey = "";
function paintInspector(box) {
  const s = sel();
  const hds = patch().filter((x) => s.has(x.head_no));
  const hd = hds.length === 1 ? hds[0] : null;
  const key = JSON.stringify(hd ? [hd.head_no, hd.name, hd.model, hd.mode, hd.universe, hd.address, hd.channels, hd.orient] : hds.map((x) => x.head_no));
  if (key === inspKey && box.firstChild) return;
  inspKey = key;
  if (!hds.length) {
    box.replaceChildren(h("p.su-insp-none", "Pick a light in the patch for its name, mode, address and which way it is hung."));
    return;
  }
  if (!hd) {
    const mv = hds.filter(moving);
    box.replaceChildren(
      h("div.su-insp-head", h("h3", `${hds.length} lights selected`)),
      h("div.su-insp-keys",
        h("button.btn.small", { type: "button", onclick: () => import("./rigtools.js").then((m) => m.openLimits(hds.map((x) => x.head_no))) }, "Limits…"),
        mv.length ? h("button.btn.small", { type: "button", onclick: () => import("./lineup.js").then((m) => m.openLineUp(mv.map((x) => x.head_no))) }, "Line up…") : null));
    return;
  }
  const n = hd.head_no;
  const name = h("input.input", { value: hd.name || hd.model, "aria-label": "Name" });
  const saveName = () => { const v = name.value.trim(); if (v && v !== (hd.name || hd.model)) run("rename_head", { head: n, name: v }, { toast: true }); };
  name.addEventListener("change", saveName);
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") name.blur(); });
  const mode = h("select.select", { "aria-label": "Mode" }, h("option", { value: hd.mode }, hd.mode || "—"));
  modesFor(hd).then((found) => {
    if (!found || !found.modes.length || inspKey !== key) return;
    // (a mode named for its channels - "33 ch" - isn't told them twice)
    mode.replaceChildren(...found.modes.map((m) => h("option", { value: m.name },
      `${m.name}${m.channel_count && !/\d\s*-?\s*ch/i.test(m.name) ? ` · ${m.channel_count} ch` : ""}`)));
    mode.value = hd.mode;
    mode.onchange = async () => {
      const want = mode.value;
      if (want === hd.mode) return;
      const ok = await confirmBox("Change mode", `Patch #${n} in the “${want}” mode? Set the real light to the same mode in its own menu - the address may move.`, { ok: "Change mode" });
      if (ok) run("change_type", { heads: [n], fixture_id: found.id, mode: want }, { toast: true });
      else mode.value = hd.mode;
    };
  });
  const at = h("input.input.su-addr", { value: addr(hd), "aria-label": "Address (universe.address)" });
  at.addEventListener("change", () => {
    const m = /^(\d+)[.:/ ](\d+)$/.exec(at.value.trim()) || (/^\d+$/.test(at.value.trim()) ? [0, hd.universe, at.value.trim()] : null);
    if (!m) { toast("Use universe.address, e.g. 1.101", "bad"); at.value = addr(hd); return; }
    run("set_address", { head: n, universe: +m[1], address: +m[2] }, { toast: true }).then((r) => { if (!r.ok) at.value = addr(hd); });
  });
  at.addEventListener("keydown", (e) => { if (e.key === "Enter") at.blur(); });
  const o = hd.orient || {};
  const sw = (key2, label) => h("label.su-switch", h("input", { type: "checkbox", checked: !!o[key2],
    onchange: (e) => run("set_orient", { heads: [n], [key2]: e.target.checked }, { toast: true }) }), h("span", label));
  box.replaceChildren(
    h("div.su-insp-head", h("h3", `${n} ${shortName(hd)}`),
      h("button.btn.ghost.small.icon", { type: "button", "aria-label": "More for this light", onclick: (e) => rowMenu(e.currentTarget, n) }, "⋯")),
    h("div.su-form",
      h("span.su-k", "Name"), name,
      h("span.su-k", "Mode"), mode,
      h("span.su-k", "Address"), h("div.su-addr-row", at, h("span.muted", `to ${lastAddr(hd)}`)),
      moving(hd) ? h("span.su-k", "Invert") : null,
      moving(hd) ? h("div.su-inv", sw("invert_pan", "Pan"), sw("invert_tilt", "Tilt")) : null),
    h("div.su-insp-keys",
      h("button.btn.small", { type: "button", onclick: () => import("./lightdialogs.js").then((m) => m.openLightTest(hd)) }, "Test this light…"),
      moving(hd) ? h("button.btn.small", { type: "button", onclick: () => import("./lineup.js").then((m) => m.openLineUp([n])) }, "Line up…") : null,
      h("button.btn.small", { type: "button", onclick: () => import("./rigtools.js").then((m) => m.openLimits([n])) }, "Limits…")));
}

// the installed fixture a patched light comes from, with its modes
const modeCache = new Map();
async function modesFor(hd) {
  const k = `${hd.manufacturer}|${hd.model}`;
  if (modeCache.has(k)) return modeCache.get(k);
  const d = await get("/api/fixtures?q=" + encodeURIComponent(`${hd.manufacturer} ${hd.model}`)).catch(() => ({}));
  const r = (d.results || []).find((x) => x.model === hd.model && x.manufacturer === hd.manufacturer) || null;
  const out = r ? { id: r.id, modes: r.modes || [] } : null;
  modeCache.set(k, out);
  return out;
}

// the selection changes many times a second while dragging in the 3D:
// only the highlight and the inspector follow it, not the whole table
let liteSel = "";
on("lite", () => {
  const grid = document.querySelector(".su-grid");
  if (!grid) return;
  const now = selected().join(",");
  if (now === liteSel) return;                  // (the feed is many times a second: only a new selection matters)
  liteSel = now;
  markSelected();
  paintInspector(grid.querySelector(".su-insp"));
  paintUniverses(grid.querySelector(".su-unis"));
});
