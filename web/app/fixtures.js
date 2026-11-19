// The fixture list: what is patched, where, and what is selected.
import { state, on, patch, selected } from "./store.js";
import { run, select } from "./actions.js";
import { $, h, menu, promptBox, confirmBox, toast, modal } from "./ui.js";
import { openAddDialog, openChannels, openProfileEditor, openCsvImport, openMotionCalibration, openLightTest } from "./dialogs.js";
import { post, get } from "./api.js";
import { isDrawn, modelDefs } from "./real3d.js";
import { openBugReport } from "./bugreport.js";
import { groupColour } from "./groupcolour.js";

let anchor = null;                 // last plain-clicked head, for shift ranges
let filterText = "";
let lastSig = "";

// each kind of light's line drawing (18 x 18): a moving head's yoke and head,
// a wash's lens rings, a laser's beam, a bar's cells, a PAR's lens...
const ICONS = {
  spot: '<path d="M4 15.5V9a5 5 0 0 1 10 0v6.5"/><circle cx="9" cy="9.5" r="2.4"/><path d="M6 15.5h6"/>',
  wash: '<path d="M4 15.5V9a5 5 0 0 1 10 0v6.5"/><circle cx="9" cy="9.5" r="2.8"/><circle cx="9" cy="9.5" r="1.1"/><path d="M6 15.5h6"/>',
  beam: '<path d="M4 15.5V9a5 5 0 0 1 10 0v6.5"/><circle cx="9" cy="9.5" r="1.5"/><path d="M6 15.5h6"/>',
  laser: '<path d="M2.5 15.5h6M4.5 15.5L15.5 4"/><path d="M9 15.5l6.5-4" opacity=".55"/>',
  par: '<circle cx="9" cy="9" r="5.5"/><circle cx="9" cy="9" r="2.2"/>',
  bar: '<rect x="1.5" y="6.5" width="15" height="5" rx="1.3"/><path d="M4.5 9h.01M7.5 9h.01M10.5 9h.01M13.5 9h.01"/>',
  profile: '<path d="M3 7h6l5-3v10l-5-3H3z"/>',
  strobe: '<rect x="2.5" y="5.5" width="13" height="7" rx="1.2"/><path d="M5 9h8"/>',
  tube: '<path d="M9 2v14"/><circle cx="9" cy="16" r="1"/>',
  confetti: '<path d="M3 15.5l3.2-8.6 5.4 5.4z"/><path d="M11 3.2l.6 1.6M14.6 6.2l1.6-.5M12.6 4.8l1.7-1.7"/>',
  cloud: '<path d="M5.2 13.5h7.6a3 3 0 0 0 .3-6 4.1 4.1 0 0 0-7.8-1.1 3.5 3.5 0 0 0-.1 7.1z"/>',
  other: '<rect x="4" y="4" width="10" height="10" rx="2"/>',
};

function iconFor(body) {
  const t = (body && body.type) || "";
  const k = t === "moving_wash" ? "wash" : t === "moving_beam" ? "beam" : t.startsWith("moving") ? "spot"
    : t.startsWith("laser") ? "laser"
      : t === "par" || t === "par_can" || t === "fresnel" || t === "wash_panel" || t === "cyc" ? "par"
        : t === "bar" || t === "matrix" ? "bar"
          : t === "profile" || t === "followspot" ? "profile"
            : t === "strobe" || t === "blinder" ? "strobe"
              : t === "tube" ? "tube"
                : t === "confetti" || t === "spark" || t === "flame" ? "confetti"
                  : t === "co2" || t === "fog" || t === "atmos" || t === "bubble" ? "cloud" : "other";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 18 18");
  svg.setAttribute("class", "type-ic");
  svg.innerHTML = ICONS[k];
  return svg;
}

function matches(hd, words) {
  if (!words.length) return true;
  const b = hd.body || {};
  const hay = [hd.head_no, hd.name, hd.manufacturer, hd.model, hd.mode, b.label, b.brand_name,
    b.type, hd.kind, `u${hd.universe}`, `${hd.universe}.${hd.address}`].join(" ").toLowerCase();
  return words.every((w) => hay.includes(w));
}

function visible() {
  const words = filterText.toLowerCase().split(/\s+/).filter(Boolean);
  return patch().filter((hd) => matches(hd, words));
}

// Lights of the same model patched one after another fold into ONE row
// ("LED PARty RGBW × 8 · 1.001-1.043"): tap it to select them all, open it
// to pick single lights.  Forty lights fit on one screen.
const FOLD_MIN = 3;
const expanded = new Set();          // first head of each opened fold

function clusters(rows) {
  const out = [];
  for (const hd of rows) {
    const last = out[out.length - 1];
    const key = [hd.model, hd.mode, hd.manufacturer].join("|");
    if (last && last.key === key) last.heads.push(hd);
    else out.push({ key, heads: [hd] });
  }
  return out;
}

const addr = (hd) => `${hd.universe}.${String(hd.address).padStart(3, "0")}`;

// a light whose 3D is a drawn placeholder (no model came with its profile)
// - its type icon gets a dashed amber frame, so the narrow list keeps its width
const DRAWN_TIP = "Placeholder 3D: the 3D view draws a stand-in for this kind of light, not the real fixture (no 3D model came with its profile). The \"placeholder 3D\" chip on the stage fetches the makers' own.";
function typeIcon(body, n) {
  const ic = iconFor(body);
  if (!isDrawn(n)) return ic;
  ic.classList.add("drawn");
  const t = document.createElementNS("http://www.w3.org/2000/svg", "title");
  t.textContent = DRAWN_TIP;
  ic.prepend(t);
  return ic;
}

// a light's own name, less the number the desk put on the end (the number has a column of its own)
const shortName = (hd) => {
  const nm = hd.name || hd.model || "";
  const cut = nm.replace(new RegExp(`\\s*#?${hd.head_no}$`), "");
  return cut || nm;
};

function headRow(hd, child = false) {
  const b = hd.body || {};
  return h("tr" + (child ? ".fold-child" : "") + (parked().includes(hd.head_no) ? ".parked" : ""),
    { dataset: { head: hd.head_no }, draggable: "true" },
    h("td.c-no", h("i.lamp"), h("span", hd.head_no)),
    h("td.c-name", h("div.f-name",
      typeIcon(b, hd.head_no),
      h("div.f-text",
        h("b", { title: [hd.name || hd.model, ...[b.brand_name && b.brand_name !== "Generic" ? b.brand_name : hd.manufacturer, b.label, hd.mode]
          .filter(Boolean)].join(" · ") }, shortName(hd))),
      hd.unverified ? h("span.warn-ic", { title: "Generic channel layout - check it matches the real fixture" }, "⚠") : null,
      h("button.btn.ghost.small.icon.row-menu", { title: "This light: test, line up, limits, rename, change type…", "aria-label": "This light's menu" }, "⋯"))),
    h("td.c-addr", addr(hd)));
}

function foldRow(c) {
  const first = c.heads[0], lastHd = c.heads[c.heads.length - 1];
  const b = first.body || {};
  const open = expanded.has(first.head_no);
  return h("tr.fold" + (open ? ".open" : ""), { dataset: { fold: c.heads.map((x) => x.head_no).join(","), head: first.head_no }, draggable: "true" },
    h("td.c-no", h("button.fold-btn", { title: open ? "Fold" : "Show each light", "aria-label": open ? "Fold" : "Show each light",
        "aria-expanded": open ? "true" : "false" }, open ? "▾" : "▸"), h("span", `${first.head_no}–${lastHd.head_no}`)),
    h("td.c-name", h("div.f-name",
      typeIcon(b, first.head_no),
      h("div.f-text",
        h("b", { title: [first.model, b.brand_name && b.brand_name !== "Generic" ? b.brand_name : first.manufacturer, b.label, first.mode]
          .filter(Boolean).join(" · ") }, first.model, h("em.f-qty", ` × ${c.heads.length}`)),
        ))),
    h("td.c-addr", h("span", addr(first)), h("small", "–" + addr(lastHd))));
}

// The groups, a list of their own above the lights: each with its colour
// (as on its fader and on the phone), its name and how many lights.  A tap
// selects its lights (Ctrl / Shift adds them or takes them out), holding
// flashes them, hovering points them out in the 3D view and a double-click
// frames them there, a right-click changes it, and lights dropped on it
// join it.  Your own groups first; the show builder's "Auto ·" ones after.
const userGroups = () => (state.snap && state.snap.groups) || [];
const isAuto = (g) => /^auto\s*·/i.test(g.name || "");
let groupSig = "";
const rowHeads = (row) => (row.dataset.heads ? row.dataset.heads.split(",").map(Number) : []);

function renderGroups(sel) {
  const list = $("#grp-list");
  const all = userGroups();
  const groups = [...all.filter((g) => !isAuto(g)), ...all.filter(isAuto)];
  const sig = JSON.stringify([groups.map((g) => [g.n, g.name, g.heads, g.colour])]);
  if (sig !== groupSig) {
    groupSig = sig;
    list.replaceChildren(...(groups.length ? groups.map((g) => {
      const sq = h("i.grp-sq", { "aria-hidden": "true" });
      sq.style.setProperty("--c", groupColour(g, all.indexOf(g)));
      const n = (g.heads || []).length;
      return h("div.grp-row" + (isAuto(g) ? ".auto" : ""), { role: "listitem", dataset: { group: g.n, heads: (g.heads || []).join(",") },
        title: `${g.name}: ${n} light${n === 1 ? "" : "s"} - tap to select them, hold to flash them, right-click to change the group` },
        sq, h("span.grp-name", g.name), h("span.grp-n", String(n)));
    }) : [h("p.grp-empty", "Select some lights, then + to make a group of them.")]));
    $("#grp-count").textContent = String(groups.length);
  }
  for (const row of list.querySelectorAll(".grp-row")) {
    const heads = rowHeads(row);
    row.classList.toggle("sel", heads.length > 0 && heads.every((x) => sel.has(x)));
  }
}

function wireGroups() {
  const list = $("#grp-list");
  list.addEventListener("click", (e) => {
    const row = e.target.closest(".grp-row");
    if (!row) return;
    const heads = rowHeads(row);
    if (!heads.length) return;
    const cur = new Set(selected());
    const all = heads.every((x) => cur.has(x));
    if (e.ctrlKey || e.metaKey || e.shiftKey) {
      if (all) select([...cur].filter((x) => !heads.includes(x))); else select(heads, { add: true });
    } else {
      select(all && cur.size === heads.length ? [] : heads);
    }
  });
  list.addEventListener("contextmenu", (e) => {
    // (a finger held on a group flashes it: no menu over the flash)
    if (e.target.closest(".grp-row.flashing")) { e.preventDefault(); return; }
    const row = e.target.closest(".grp-row");
    const g = row && userGroups().find((x) => x.n === +row.dataset.group);
    if (g) { e.preventDefault(); groupMenu(atPointer(e), g); }
  });
  list.addEventListener("dblclick", (e) => {
    const row = e.target.closest(".grp-row");
    if (row && window.hazeStage) window.hazeStage.frame(rowHeads(row));
  });
  // a group under the pointer: its lights pointed out in the 3D view
  let peeking = null;
  const peek = (row) => {
    if (row === peeking) return;
    peeking = row;
    if (window.hazeStage && window.hazeStage.setPeek) window.hazeStage.setPeek(row ? rowHeads(row) : []);
  };
  list.addEventListener("pointerover", (e) => peek(e.target.closest(".grp-row")));
  list.addEventListener("pointerleave", () => peek(null));
  holdToFlash(list);
  list.addEventListener("dragover", dragOver);
  list.addEventListener("drop", drop);
}

// Hold a group's line to flash its lights - full, over everything else -
// until you let go (a tap still selects them).  The release is caught on
// the window: the list may be redrawn while it is held.
function holdToFlash(list) {
  let timer = 0, row = null;
  const stop = () => { clearTimeout(timer); row = null; };
  list.addEventListener("pointerdown", (e) => {
    const tr = e.target.closest(".grp-row");
    if (!tr || e.button !== 0) return;
    stop();
    row = tr;
    const group = +tr.dataset.group;
    timer = setTimeout(() => {
      if (row !== tr) return;
      tr.classList.add("flashing");
      run("group_flash", { group, down: true }, { silentError: true });
      const swallow = (ev) => { ev.stopImmediatePropagation(); ev.preventDefault(); };
      const up = (ev) => {
        window.removeEventListener("pointerup", up, true);
        window.removeEventListener("pointercancel", up, true);
        tr.classList.remove("flashing");
        run("group_flash", { down: false }, { silentError: true });
        // the click that follows a hold is not a tap
        if (ev.type === "pointerup") {
          window.addEventListener("click", swallow, { capture: true, once: true });
          setTimeout(() => window.removeEventListener("click", swallow, { capture: true }), 0);
        }
      };
      window.addEventListener("pointerup", up, true);
      window.addEventListener("pointercancel", up, true);
    }, 350);
  });
  list.addEventListener("pointerup", stop);
  list.addEventListener("pointercancel", stop);
  list.addEventListener("pointerout", (e) => { if (row && !row.contains(e.relatedTarget)) stop(); });
}

function render() {
  const rows = visible();
  const sel = new Set(selected());
  const stale = new Set(((state.snap && state.snap.stale_heads) || []).map(Number));
  const tbody = $("#fx-rows");
  const sig = JSON.stringify([rows.map((r) => [r.head_no, r.name, r.universe, r.address, r.model, r.mode, r.body && r.body.type, r.unverified]),
    [...stale], [...expanded], parked(), filterText, [...(state.drawn3d || [])]]);
  if (sig !== lastSig) {
    lastSig = sig;
    const out = [];
    for (const c of clusters(rows)) {
      if (c.heads.length < FOLD_MIN) { out.push(...c.heads.map((hd) => headRow(hd))); continue; }
      out.push(foldRow(c));
      if (expanded.has(c.heads[0].head_no)) out.push(...c.heads.map((hd) => headRow(hd, true)));
    }
    tbody.replaceChildren(...out);
  }
  for (const tr of tbody.children) {
    if (tr.dataset.fold) {
      const members = tr.dataset.fold.split(",").map(Number);
      const n = members.filter((x) => sel.has(x)).length;
      tr.classList.toggle("sel", n === members.length);
      tr.classList.toggle("part", n > 0 && n < members.length);
    } else tr.classList.toggle("sel", sel.has(+tr.dataset.head));
  }
  const n = patch().length;
  $("#fx-count").textContent = n;
  $(".fx-table-wrap").classList.toggle("is-empty", n === 0);
  renderGroups(sel);
  renderSelBar(sel);
  paintLamps();
  snugSoon();
}

// A list that ALMOST fits (a few pixels short - the last row's padding and
// border) got a scrollbar to scroll those few pixels: it doesn't now.  (A
// list that really doesn't fit scrolls as before.)
let snugQueued = false;
function snugSoon() {
  if (snugQueued) return;
  snugQueued = true;
  requestAnimationFrame(() => { snugQueued = false; snug(); });
}
function snug() {
  const wrap = $(".fx-table-wrap");
  if (!wrap) return;
  const over = wrap.scrollHeight - wrap.clientHeight;
  // (measured with the scrollbar allowed, so a list that grows past it scrolls again)
  if (wrap.classList.contains("snug")) {
    wrap.classList.remove("snug");
    const real = wrap.scrollHeight - wrap.clientHeight;
    wrap.classList.toggle("snug", real > 0 && real <= 8);
    return;
  }
  wrap.classList.toggle("snug", over > 0 && over <= 8);
}
if (typeof ResizeObserver !== "undefined") {
  requestAnimationFrame(() => { const w = $(".fx-table-wrap"); if (w) new ResizeObserver(snugSoon).observe(w); });
}

// "6 selected · LED PARs" and, for two or more, the quick splits
function renderSelBar(sel) {
  const count = sel.size;
  const user = userGroups();
  const same = (heads) => heads.length === count && heads.every((x) => sel.has(x));
  // named by its group when it is one: the operator's, or one the desk makes (by kind, by truss)
  const autos = (state.snap && state.snap.auto_groups) || [];
  const named = count ? user.find((g) => same(g.heads || [])) || autos.find((g) => same(g.heads || [])) : null;
  const c = $("#sel-count");
  c.textContent = count ? `${count} selected` : "Nothing selected";
  c.title = named ? `${named.name}` : "";
  for (const b of document.querySelectorAll("#sel-picks [data-split]")) b.disabled = count < 2;
}

// The selection's Group…: a new group of them, or add them to one, or take
// them out of one
function selGroupMenu(anchor) {
  const sel = selected();
  if (!sel.length) { toast("Select lights first"); return; }
  const groups = userGroups().filter((g) => !isAuto(g));
  const addTo = groups.filter((g) => sel.some((x) => !(g.heads || []).includes(x)));
  const outOf = groups.filter((g) => sel.some((x) => (g.heads || []).includes(x)));
  menu(anchor, [
    { label: `Make a group of them…`, hint: "G", run: makeGroup },
    ...(addTo.length ? ["-", ...addTo.slice(0, 12).map((g) => ({ label: `Add them to “${g.name}”`,
      run: () => run("group_edit", { group: g.n, add: sel }, { toast: true }) }))] : []),
    ...(outOf.length ? ["-", ...outOf.slice(0, 12).map((g) => ({ label: `Take them out of “${g.name}”`,
      run: () => run("group_edit", { group: g.n, remove: sel.filter((x) => (g.heads || []).includes(x)) }, { toast: true }) }))] : []),
  ]);
}

// The row lamps follow the light feed (30 times a second), so only a
// lamp whose colour or level actually changed is written: restyling every
// row every frame is what made the list the busiest thing on the page.
let lampQueued = false;
function paintLamps() {
  if (lampQueued) return;
  lampQueued = true;
  requestAnimationFrame(() => {
    lampQueued = false;
    const looks = state.looks || {};
    for (const tr of $("#fx-rows").children) {
      const lamp = tr.querySelector(".lamp");
      if (!lamp) continue;
      const lk = looks[tr.dataset.head];
      const key = lk && lk.a > 0 ? lk.hex + (Math.round(lk.a * 20) / 20) : "";
      if (lamp.dataset.k === key) continue;
      lamp.dataset.k = key;
      if (key) {
        lamp.style.background = lk.hex;
        lamp.style.opacity = String(0.35 + lk.a * 0.65);
        lamp.style.boxShadow = `0 0 ${Math.round(4 + lk.a * 8)}px ${lk.hex}`;
      } else {
        lamp.style.background = "";
        lamp.style.opacity = "";
        lamp.style.boxShadow = "";
      }
    }
  });
}

const autoCopies = () => userGroups().filter((g) => /^auto\s*·/i.test(g.name || ""));

// One group's menu (a right-click on it in the Groups list):
// change the lights in it from the selection, rename, delete.
export function groupMenu(anchor, g) {
  const sel = selected();
  const inG = new Set(g.heads);
  const add = sel.filter((x) => !inG.has(x));
  const out = sel.filter((x) => inG.has(x));
  const same = sel.length === g.heads.length && !add.length;
  menu(anchor, [
    { label: add.length ? `Add the ${add.length} selected light(s)` : "Add the selected lights", hint: sel.length ? (add.length ? "" : "all already in it") : "select lights first",
      disabled: !add.length, run: () => run("group_edit", { group: g.n, add: add }, { toast: true }) },
    { label: out.length ? `Take the ${out.length} selected light(s) out` : "Take the selected lights out", hint: out.length && out.length === g.heads.length ? "that's all of them: delete instead" : (out.length ? "" : "none of the selection is in it"),
      disabled: !out.length || out.length === g.heads.length, run: () => run("group_edit", { group: g.n, remove: out }, { toast: true }) },
    { label: `Make it exactly the selection${sel.length ? ` (${sel.length})` : ""}`, disabled: !sel.length || same,
      run: () => run("group_edit", { group: g.n, heads: sel }, { toast: true }) },
    { label: "Select its lights", run: () => select(g.heads) },
    { label: "Faders on the buttons page", hint: "effect speed, strobe, strobe speed",
      run: () => import("./quickbuttons.js").then((m) => run("quick_group_controls", { group: g.n, page: m.currentPage() }, { toast: true })) },
    { label: "Rename…", run: async () => {
      const name = await promptBox("Rename group", "Name", g.name, { ok: "Rename" });
      if (name && name.trim() && name.trim() !== g.name) run("group_edit", { group: g.n, name: name.trim() }, { toast: true });
    } },
    "-",
    { label: "Delete group…", danger: true, run: async () => {
      if (await confirmBox("Delete group", `Delete the group “${g.name}”? The fixtures stay patched.`, { ok: "Delete", danger: true })) {
        run("group_delete", { group: g.n });
      }
    } },
  ]);
}

function rowClick(e) {
  const tr = e.target.closest("tr[data-head]");
  if (!tr) return;
  if (tr.dataset.fold) {
    const members = tr.dataset.fold.split(",").map(Number);
    if (e.target.closest(".fold-btn")) {
      const first = members[0];
      if (expanded.has(first)) expanded.delete(first); else expanded.add(first);
      render();
      return;
    }
    const cur = new Set(selected());
    const all = members.every((x) => cur.has(x));
    if (e.ctrlKey || e.metaKey || e.shiftKey) {
      if (all) select([...cur].filter((x) => !members.includes(x))); else select(members, { add: true });
    } else {
      select(all && cur.size === members.length ? [] : members);
    }
    return;
  }
  const head = +tr.dataset.head;
  if (e.target.closest(".row-menu")) return rowMenu(e.target.closest(".row-menu"), head);
  const cur = new Set(selected());
  if (e.shiftKey && anchor !== null) {
    const nums = visible().map((x) => x.head_no);
    const a = nums.indexOf(anchor), b = nums.indexOf(head);
    if (a >= 0 && b >= 0) {
      select(nums.slice(Math.min(a, b), Math.max(a, b) + 1), { add: e.ctrlKey || e.metaKey });
      return;
    }
  }
  anchor = head;
  if (e.ctrlKey || e.metaKey) {
    if (cur.has(head)) { cur.delete(head); select([...cur]); } else select([head], { add: true });
  } else if (cur.size === 1 && cur.has(head)) {
    select([]);
  } else {
    select([head]);
  }
}

// the row's light, or the whole selection when the row is part of it
function sameSelection(head) {
  const sel = selected();
  return sel.includes(head) ? sel : [head];
}

// Change the fixture type of patched lights: their number, place,
// groups, cues and looks stay; the address stays if the new one fits.
function openChangeType(heads) {
  const search = h("input.input", { type: "search", placeholder: "Search your installed fixtures…" });
  const list = h("div.ct-list");
  const note = h("p.muted.small", heads.length > 1 ? `${heads.length} lights will change.` : "");
  let close = null, seq = 0;
  const pick = async (r, mode) => {
    const res = await run("change_type", { heads, fixture_id: r.id, mode: mode ? mode.name : undefined }, { toast: true });
    if (res.ok) close();
  };
  const find = async () => {
    const my = ++seq;
    const d = await get("/api/fixtures?q=" + encodeURIComponent(search.value.trim())).catch(() => ({}));
    if (my !== seq) return;
    const rows = (d.results || []).slice(0, 40);
    list.replaceChildren(...(rows.length ? rows.map((r) => h("div.ct-item",
      h("b", `${r.manufacturer} ${r.model}`),
      h("div.chip-row", ...((r.modes || []).length ? r.modes : [null]).map((m) => h("button.chip", {
        onclick: () => pick(r, m) }, m ? `${m.name} · ${m.channel_count} ch` : "Use this")))))
      : [h("p.muted.small", "Nothing installed matches - add the fixture from + Add first.")]));
  };
  let t = 0;
  search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(find, 150); });
  close = modal({ title: "Change fixture type", body: h("div", note, search, list) });
  find();
  search.focus();
}

/** Somewhere for a menu to open: where the mouse is. */
export const atPointer = (e) => ({ getBoundingClientRect: () => ({ left: e.clientX, top: e.clientY, bottom: e.clientY }) });

/** A light's menu (its ⋯ button, a right-click here or on the stage). */
// A light's menu: put it (and the lights selected with it) in a group, or
// take it out of one.
function groupItems(head) {
  const groups = userGroups();
  if (!groups.length) return [];
  const sel = selected();
  const heads = sel.includes(head) ? sel : [head];
  const what = heads.length > 1 ? `these ${heads.length}` : "it";
  const items = [];
  for (const g of groups.slice(0, 16)) {
    const inG = new Set(g.heads);
    if (heads.some((x) => !inG.has(x))) items.push({ label: `Add ${what} to “${g.name}”`, run: () => run("group_edit", { group: g.n, add: heads }, { toast: true }) });
  }
  for (const g of groups) {
    if (!g.heads.includes(head)) continue;
    const out = heads.filter((x) => g.heads.includes(x));
    if (out.length < g.heads.length) items.push({ label: `Take ${out.length > 1 ? `these ${out.length}` : "it"} out of “${g.name}”`, run: () => run("group_edit", { group: g.n, remove: out }, { toast: true }) });
  }
  return items.length ? ["-", ...items, "-"] : [];
}

export function rowMenu(btn, head) {
  const hd = patch().find((x) => x.head_no === head);
  if (!hd) return;
  menu(btn, [
    { label: "Rename…", run: async () => {
      const name = await promptBox("Rename fixture", "Name", hd.name || "", { ok: "Rename" });
      if (name) run("rename_head", { head, name });
    } },
    { label: "Change DMX address…", run: async () => {
      const v = await promptBox("DMX address", "Universe.address (e.g. 1.101)", `${hd.universe}.${hd.address}`);
      if (!v) return;
      const m = /^(\d+)[.:/ ](\d+)$/.exec(v.trim());
      if (!m) { toast("Use universe.address, e.g. 2.1", "bad"); return; }
      run("set_address", { head, universe: +m[1], address: +m[2] }, { toast: true });
    } },
    { label: "Test this light…", run: () => openLightTest(hd) },
    { label: "Show DMX channels", run: () => openChannels([head]) },
    { label: "Edit fixture profile…", run: () => openProfileEditor(hd) },
    { label: "Change fixture type…", run: () => openChangeType(sameSelection(head)) },
    ...(hd.photo ? [{ label: "Light output in the 3D…", hint: `${hd.photo.lm} lm${hd.photo.lm_from === "estimate" ? " (estimated)" : hd.photo.lm_from === "yours" ? " (yours)" : ""}`,
      run: () => import("./lightdialogs.js").then((m) => m.openLightOutput(hd)) }] : []),
    ...((hd.map || []).some((r) => r === "pan" || r === "tilt")
      ? [{ label: "Calibrate movement speed…", run: () => openMotionCalibration(hd) },
        { label: "Line up with the real light…", hint: hd.align ? "lined up" : "point it at floor marks", run: () => import("./lineup.js").then((m) => m.openLineUp(sameSelection(head))) },
        { label: ((hd.orient || {}).invert_pan ? "✓ " : "") + "Invert pan", hint: "it pans the other way", run: () => run("set_orient", { heads: sameSelection(head), invert_pan: !(hd.orient || {}).invert_pan }, { toast: true }) },
        { label: ((hd.orient || {}).invert_tilt ? "✓ " : "") + "Invert tilt", hint: "it tilts the other way", run: () => run("set_orient", { heads: sameSelection(head), invert_tilt: !(hd.orient || {}).invert_tilt }, { toast: true }) }] : []),
    { label: "Limits and orientation…", hint: "how far it may go, which way up", run: () => import("./rigtools.js").then((m) => m.openLimits(sameSelection(head))) },
    { label: "Select all of this type", run: () => run("select_similar", { head }) },
    { label: "Make a colour row of buttons…", hint: "one button per colour, one at a time", run: () => import("./quickbuttons.js").then((m) => m.makeColourRow(sameSelection(head))) },
    ...groupItems(head),
    { label: "Frame on stage", run: () => window.hazeStage && window.hazeStage.frame([head]) },
    { label: "Report a problem with this light…", hint: "its file, DMX and 3D go with it", run: () => openBugReport(head) },
    "-",
    { label: "Remove from patch", danger: true, run: async () => {
      if (await confirmBox("Remove fixture", `Remove #${head} ${hd.name || hd.model} from the patch?\nCtrl+Z brings it back.`, { ok: "Remove", danger: true })) {
        run("remove_heads", { heads: [head] });
      }
    } },
  ]);
}

// A folded row (8 movers shown as one): the same tools, for all of them
function foldMenu(anchor, heads) {
  const first = patch().find((x) => x.head_no === heads[0]);
  if (!first) return;
  const moving = (first.map || []).some((r) => r === "pan" || r === "tilt");
  const o = first.orient || {};
  const n = lights(heads.length);
  const groups = userGroups();
  menu(anchor, [
    { label: `Select these ${n}`, run: () => select(heads) },
    { label: "Test them…", run: () => openLightTest(first) },
    { label: "Show their DMX channels", run: () => openChannels(heads) },
    { label: "Limits and orientation…", run: () => import("./rigtools.js").then((m) => m.openLimits(heads)) },
    { label: "Change their fixture type…", run: () => openChangeType(heads) },
    ...(moving ? [
      { label: "Line up with the real lights…", run: () => import("./lineup.js").then((m) => m.openLineUp(heads)) },
      { label: (o.invert_pan ? "✓ " : "") + "Invert pan, all of them", run: () => run("set_orient", { heads, invert_pan: !o.invert_pan }, { toast: true }) },
      { label: (o.invert_tilt ? "✓ " : "") + "Invert tilt, all of them", run: () => run("set_orient", { heads, invert_tilt: !o.invert_tilt }, { toast: true }) }] : []),
    { label: "Make a group of them…", run: async () => {
      const name = await promptBox("New group", "Name", `${first.body && first.body.label ? first.body.label : first.model} ×${heads.length}`, { ok: "Make group" });
      if (name && name.trim()) run("group_create", { name: name.trim(), heads }, { toast: true });
    } },
    ...groups.slice(0, 8).map((g) => ({ label: `Add them to “${g.name}”`, run: () => run("group_edit", { group: g.n, add: heads }, { toast: true }) })),
    { label: "Make a colour row of buttons…", run: () => import("./quickbuttons.js").then((m) => m.makeColourRow(heads)) },
    { label: "Frame on stage", run: () => window.hazeStage && window.hazeStage.frame(heads) },
    "-",
    { label: `Remove ${n} from the patch`, danger: true, run: async () => {
      if (await confirmBox("Remove from the patch", `Remove ${n} from the patch? Their cues and palettes forget them. Ctrl+Z brings them back.`, { ok: `Remove ${n}`, danger: true })) run("remove_heads", { heads }, { toast: true });
    } },
  ]);
}

// ---------------------------------------------------------- DMX map
// Who owns which channels, universe by universe; clashes in red, each with
// a one-tap "move to the next free address".
const clashes = () => (state.snap && state.snap.clashes) || [];
const hue = (n) => `hsl(${(n * 67) % 360} 55% 42%)`;

function renderClashes() {
  const box = $("#fx-clash");
  const list = clashes();
  const key = JSON.stringify(list);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.hidden = !list.length;
  box.replaceChildren(...(list.length ? [
    h("b", `⚠ ${list.length} address clash${list.length > 1 ? "es" : ""}`),
    ...list.slice(0, 3).map((c) => h("div.clash-row",
      h("span", `#${c.a} and #${c.b} share ${c.universe}.${String(c.from).padStart(3, "0")}–${String(c.to).padStart(3, "0")}`),
      h("button.btn.small", { title: `Move #${c.b} to the first free block of addresses`,
        onclick: () => run("patch_move_free", { head: c.b }, { toast: true }) }, `Move #${c.b}`))),
    h("button.btn.small.ghost", { onclick: openDmxMap }, "Open the DMX map"),
  ] : []));
}

export function openDmxMap() {
  const draw = () => {
    const heads = patch();
    const bad = new Set(clashes().flatMap((c) => [c.a, c.b]));
    const unis = [...new Set(heads.map((x) => x.universe))].sort((a, b) => a - b);
    if (!unis.length) unis.push(1);
    const blocks = unis.map((u) => {
      const own = new Array(513).fill(null);
      const clash = new Array(513).fill(false);
      for (const x of heads.filter((y) => y.universe === u)) {
        for (let c = x.address; c < x.address + (x.channels || (x.map || []).length || 1) && c <= 512; c++) {
          if (own[c] !== null) clash[c] = true;
          own[c] = x.head_no;
        }
      }
      const used = own.filter((x) => x !== null).length;
      const cells = [];
      for (let c = 1; c <= 512; c++) {
        const n = own[c];
        const hd = n !== null ? heads.find((y) => y.head_no === n) : null;
        cells.push(h("i" + (clash[c] ? ".clash" : n !== null ? ".own" : ""), {
          title: `${u}.${String(c).padStart(3, "0")}` + (hd ? ` · #${n} ${hd.name || hd.model}` : " · free")
            + (clash[c] ? " · CLASH" : ""),
          style: n !== null && !clash[c] ? { background: hue(n) } : null,
        }));
      }
      return h("div.dmx-uni", h("h4", `Universe ${u}`, h("small", ` ${used} of 512 channels used`)), h("div.dmx-grid", ...cells));
    });
    const rows = heads.slice().sort((a, b) => a.universe - b.universe || a.address - b.address).map((x) => {
      const n = x.channels || (x.map || []).length || 1;
      return h("div.dmx-row" + (bad.has(x.head_no) ? ".bad" : ""),
        h("i", { style: { background: bad.has(x.head_no) ? "var(--live)" : hue(x.head_no) } }),
        h("span.mono", `${x.universe}.${String(x.address).padStart(3, "0")}–${String(x.address + n - 1).padStart(3, "0")}`),
        h("span", `#${x.head_no} ${x.name || x.model}`), h("span.muted.small", `${n} ch`),
        bad.has(x.head_no) ? h("button.btn.small", { onclick: () => run("patch_move_free", { head: x.head_no }, { toast: true }) }, "Move to free") : null);
    });
    return h("div.dmx-map", ...blocks, h("div.dmx-list", ...rows));
  };
  const body = h("div", draw());
  const close = modal({ title: "DMX map", wide: true, body,
    foot: [h("span.muted.small", "Each square is one DMX channel. Red: two lights on the same channels. After moving a light, set the same address on the light itself."),
      h("span.grow"), h("button.btn", { onclick: () => close() }, "Close")] });
  const off = on("snapshot", () => { if (!document.body.contains(body)) { off && off(); return; } body.replaceChildren(draw()); });
}

// RDM: the lights say what they are, where they're addressed and in which
// mode; the list compares that with the patch (needs an RDM node).
export async function openRdm() {
  const body = h("div.rdm", h("p.muted", "Asking the lights… (a few seconds)"));
  const close = modal({ title: "Ask the lights (RDM)", wide: true, body });
  const d = await post("/api/console/rdm", {}).catch((e) => ({ error: e.message }));
  const r = d.result || {};
  if (d.error || !r.ok) {
    body.replaceChildren(h("p.warn", d.error || r.error || "RDM failed"));
    return;
  }
  const ICON = { ok: "✓", different: "!", new: "+", silent: "?" };
  const setAddr = async (dev) => {
    const v = await promptBox("Set the light's address", `DMX address on universe ${dev.universe} (1-512)`,
      dev.address ? String(dev.address) : "", { ok: "Send to the light" });
    if (!v) return;
    const res = await post("/api/console/rdm", { set_address: { uid: dev.uid, universe: dev.universe, address: +v } })
      .catch((e) => ({ error: e.message }));
    const rr = res.result || {};
    toast(res.error || rr.error || `The light is now at ${dev.universe}.${v}`, res.error || rr.error ? "bad" : "ok");
  };
  const row = (dev) => h("div.rdm-row." + dev.status,
    h("span.ready-ic", ICON[dev.status] || "·"),
    h("div", h("b", dev.name || `#${dev.head}`),
      h("small.muted", [dev.uid, dev.universe && dev.address ? `${dev.universe}.${String(dev.address).padStart(3, "0")}` : "",
        dev.footprint ? `${dev.footprint} ch` : "", dev.mode || ""].filter(Boolean).join(" · ")),
      h("div.small", dev.note || "")),
    h("div.row-btns",
      dev.status === "new" ? h("button.btn.small", { onclick: () => {
        close();
        run("add_heads", { query: dev.name, universe: dev.universe, address: dev.address }, { toast: true });
      } }, "Add it") : null,
      dev.status === "different" ? h("button.btn.small", { onclick: () => { close(); openChangeType([dev.head]); } }, "Change type…") : null,
      dev.uid ? h("button.btn.small.ghost", { onclick: () => setAddr(dev) }, "Set address…") : null));
  const devs = r.devices || [];
  body.replaceChildren(
    devs.length ? h("p.muted.small", r.summary) : null,
    ...(devs.length ? devs.map(row) : [h("p.muted", "No light answered. RDM needs a node with RDM switched on, and lights that support it"
      + (r.tried && r.tried !== "255.255.255.255" ? ` (asked ${r.tried}).` : "."))]),
    ...((r.silent || []).length ? [h("h3", "Patched, but no answer"), ...r.silent.map(row)] : []));
}

const parked = () => (state.snap && state.snap.parked) || [];
const lights = (n) => `${n} light${n === 1 ? "" : "s"}`;

async function toolsMenu(btn) {
  const sel = selected();
  const { selectItems } = await import("./rigtools.js");
  const hl = (state.snap && state.snap.highlight) || {};
  menu(btn, [
    ...selectItems(),
    { label: hl.on ? "Highlight off" : "Highlight the selection", hint: "H", disabled: !hl.on && !sel.length,
      run: () => run("highlight", { state: !hl.on }, { toast: true }) },
    { label: "Solo the selection", hint: "Shift+H · the rest dark", disabled: !sel.length,
      run: () => run("highlight", { state: true, solo: true }, { toast: true }) },
    "-",
    { label: "Add lights…", run: () => openAddDialog() },
    { label: "DMX map…", hint: clashes().length ? `${clashes().length} clash(es)` : "who uses which channels", run: openDmxMap },
    { label: "Find nodes", hint: "the DMX nodes on the network", run: () => scanRig() },
    { label: "Ask the lights (RDM)…", hint: "model, mode and address from each light", run: () => openRdm() },
    ...(autoCopies().length ? [{ label: `Tidy ${autoCopies().length} auto group copies`,
      hint: "old “Auto ·” copies the show builder made", run: async () => {
        const n = autoCopies().length;
        if (await confirmBox("Tidy auto groups", `Delete the ${n} “Auto ·” group copies the show builder made? Your own groups, cues and buttons stay. Ctrl+Z brings them back.`, { ok: "Tidy away", danger: true })) {
          run("group_delete", { auto: true }, { toast: true });
        }
      } }] : []),
    { label: "Auto-address the patch", run: async () => {
      if (await confirmBox("Auto-address", "Re-address every fixture from 1.001 with no gaps or overlaps?\nCtrl+Z undoes it.", { ok: "Re-address" })) run("auto_patch", {}, { toast: true });
    } },
    "-",
    { label: "Export patch as CSV", run: () => exportCsv() },
    { label: "Import patch from CSV…", run: () => openCsvImport() },
    { label: "DMX channels of the selection", disabled: !sel.length, run: () => openChannels(sel) },
    "-",
    { label: "Park the selection dark", hint: "held off whatever runs", disabled: !sel.length, run: () => run("park", { heads: sel, mode: "dark" }, { toast: true }) },
    { label: "Park the selection as it is", hint: "frozen, whatever runs", disabled: !sel.length, run: () => run("park", { heads: sel, mode: "hold" }, { toast: true }) },
    { label: "Unpark the selection", disabled: !sel.length || !sel.some((n) => parked().includes(n)), run: () => run("unpark", { heads: sel }, { toast: true }) },
    { label: `Unpark all${parked().length ? ` (${parked().length})` : ""}`, disabled: !parked().length, run: () => run("unpark", { all: true }, { toast: true }) },
    { label: "Clear the selection's values", hint: "the rest of the programmer stays", disabled: !sel.length, run: () => run("clear_heads", { heads: sel }, { toast: true }) },
    "-",
    // arranging the selected lights in the room (one Ctrl+Z each)
    { label: "Arrange: in a row across the stage", hint: "same distance from the front", disabled: sel.length < 2, run: () => run("align", { heads: sel, axis: "z" }, { toast: true }) },
    { label: "Arrange: all at the same height", disabled: sel.length < 2, run: () => run("align", { heads: sel, axis: "y" }, { toast: true }) },
    { label: "Arrange the selection…", hint: "line up, spread, mirror", disabled: sel.length < 2, run: () => import("./rigtools.js").then((m) => m.openArrange()) },
    { label: "Limits and orientation…", hint: "how far it may go, which way up", disabled: !sel.length, run: () => import("./rigtools.js").then((m) => m.openLimits(sel)) },
    { label: "Arrange: space evenly left to right", hint: "between the outermost two", disabled: sel.length < 3, run: () => run("distribute", { heads: sel, axis: "x" }, { toast: true }) },
    { label: "Arrange: space evenly front to back", disabled: sel.length < 3, run: () => run("distribute", { heads: sel, axis: "z" }, { toast: true }) },
    { label: "Arrange: flip left ↔ right", hint: "about its own middle", disabled: sel.length < 2, run: () => run("mirror", { heads: sel, axis: "x" }, { toast: true }) },
    "-",
    // the two that take lights out of the show: last, in red, the count said
    // on the menu and again on the button that does it
    { label: sel.length ? `Remove ${lights(sel.length)} from the patch` : "Remove the selection from the patch", danger: true, disabled: !sel.length, run: async () => {
      if (await confirmBox("Remove from the patch", `Remove ${lights(sel.length)} from the patch? Their cues and palettes forget them. Ctrl+Z brings them back.`, { ok: `Remove ${lights(sel.length)}`, danger: true })) run("remove_heads", { heads: sel }, { toast: true });
    } },
    { label: `Clear the whole patch${patch().length ? ` (${lights(patch().length)})` : ""}`, danger: true, disabled: !patch().length, run: async () => {
      const n = patch().length;
      if (await confirmBox("Clear the whole patch", `Remove all ${lights(n)} from the patch? Every cue and palette loses them. Ctrl+Z brings them back.`, { ok: `Remove all ${lights(n)}`, danger: true })) post("/api/console/patch", { action: "clear" }).then(() => toast("Patch cleared - Ctrl+Z brings it back"));
    } },
  ]);
}

async function exportCsv() {
  const r = await run("export_patch", {});
  if (!r.ok) return;
  const text = r.csv || "";
  if (!text) { toast(r.summary || "Exported", "ok"); return; }
  const blob = new Blob([text], { type: "text/csv" });
  const a = h("a", { href: URL.createObjectURL(blob), download: ((state.snap && state.snap.show_file) || "patch") + ".csv" });
  document.body.append(a);
  a.click();
  a.remove();
}

export async function scanRig() {
  toast("Scanning the network for Art-Net nodes…");
  try {
    const d = await post("/api/console/scan", { import: false });
    const r = d.result || {};
    const nodes = r.nodes || [];
    if (!nodes.length) {
      toast(r.message || r.scan_error || "No Art-Net nodes answered. Check the network cable and that the node is on the same subnet.", "bad", 7000);
      return;
    }
    toast(`${nodes.length} node(s) found: ${nodes.map((n) => (n.name || n.short_name || n.ip)).join(", ")}`, "ok", 7000);
  } catch (err) {
    toast(err.message, "bad");
  }
}

// ---------------------------------------------------------- drag and drop
// Drag a light (the selection when it is selected, or a folded run) onto a
// group to put it in.  (A group's menu takes lights out.)
const DRAG = "application/x-haze-heads";
let dragging = null;                     // {heads}
function dragStart(e) {
  const tr = e.target.closest && e.target.closest("tr[data-head]");
  if (!tr) return;
  let heads = tr.dataset.fold ? tr.dataset.fold.split(",").map(Number) : [+tr.dataset.head];
  const sel = selected();
  if (!tr.dataset.fold && sel.includes(heads[0]) && sel.length > 1) heads = [...sel];
  dragging = { heads };
  e.dataTransfer.setData(DRAG, JSON.stringify(dragging));
  e.dataTransfer.setData("text/plain", heads.map((n) => "#" + n).join(" "));
  e.dataTransfer.effectAllowed = "copy";
  document.body.classList.add("dragging-heads");
}
function dragEnd() {
  dragging = null;
  document.body.classList.remove("dragging-heads");
  for (const el of document.querySelectorAll(".drop-on")) el.classList.remove("drop-on");
}
// where a drop would go: a group's row
function dropTarget(el) {
  if (!dragging || !el || !el.closest) return null;
  const row = el.closest(".grp-row");
  return row ? { el: row, to: +row.dataset.group } : null;
}
function dragOver(e) {
  const t = dropTarget(e.target);
  for (const el of document.querySelectorAll(".drop-on")) if (!t || el !== t.el) el.classList.remove("drop-on");
  if (!t) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "copy";
  t.el.classList.add("drop-on");
}
async function drop(e) {
  const t = dropTarget(e.target);
  const d = dragging;
  dragEnd();
  if (!t || !d) return;
  e.preventDefault();
  run("group_edit", { group: t.to, add: d.heads }, { toast: true });
}
function wireDrag() {
  $("#fx-rows").addEventListener("dragstart", dragStart);
  $("#fx-rows").addEventListener("dragend", dragEnd);
}

async function makeGroup() {
  const sel = selected();
  if (!sel.length) { toast("Select fixtures first, then make a group"); return; }
  const name = await promptBox("New group", `Name for these ${sel.length} fixture(s)`, "", { ok: "Create group", placeholder: "e.g. Back truss spots" });
  if (name) run("group_create", { name, heads: sel });
}

export function initFixtures() {
  $("#fx-rows").addEventListener("click", rowClick);
  wireDrag();
  wireGroups();
  $("#fx-rows").addEventListener("contextmenu", (e) => {
    const tr = e.target.closest("tr[data-head]");
    if (!tr) return;
    e.preventDefault();
    if (tr.dataset.fold) foldMenu(atPointer(e), tr.dataset.fold.split(",").map(Number));
    else rowMenu(atPointer(e), +tr.dataset.head);
  });
  $("#fx-rows").addEventListener("dblclick", (e) => {
    const tr = e.target.closest("tr[data-head]");
    if (!tr || !window.hazeStage) return;
    window.hazeStage.frame(tr.dataset.fold ? tr.dataset.fold.split(",").map(Number) : [+tr.dataset.head]);
  });
  // find a light: the field opens under the heading, Esc closes it
  const findBox = $("#fx-toolbar"), find = $("#fx-filter"), findBtn = $("#fx-find");
  const showFind = (on) => {
    findBox.hidden = !on;
    findBtn.classList.toggle("on", on);
    findBtn.setAttribute("aria-expanded", String(on));
    if (on) find.focus();
    else if (find.value) { find.value = ""; filterText = ""; render(); }
  };
  findBtn.addEventListener("click", () => showFind(findBox.hidden));
  find.addEventListener("input", (e) => { filterText = e.target.value; render(); });
  find.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const heads = visible().map((x) => x.head_no);
      if (heads.length) select(heads);
    }
    if (e.key === "Escape") { e.stopPropagation(); showFind(false); }
  });
  $("#add-btn").addEventListener("click", () => openAddDialog());
  $("#add-btn-2").addEventListener("click", () => openAddDialog());
  $("#fx-more").addEventListener("click", (e) => toolsMenu(e.currentTarget));
  $("#group-btn").addEventListener("click", makeGroup);
  $("#sel-group").addEventListener("click", (e) => selGroupMenu(e.currentTarget));
  for (const b of document.querySelectorAll("#sel-picks [data-act]")) {
    b.addEventListener("click", () => (b.dataset.act === "clear_selection" ? select([]) : run(b.dataset.act)));
  }
  for (const b of document.querySelectorAll("#sel-picks [data-split]")) {
    b.addEventListener("click", () => run("select_split", { split: b.dataset.split }));
  }
  on("snapshot", () => { modelDefs(); render(); renderClashes(); });
  on("models3d", render);
  on("selection", () => { render(); });
  on("lite", render);
  on("looks", paintLamps);
  void get;
}

export { makeGroup };
