// The fixture list: what is patched, where, and what is selected.
import { state, on, patch, selected } from "./store.js";
import { run, select } from "./actions.js";
import { $, h, menu, promptBox, confirmBox, toast, modal } from "./ui.js";
import { openAddDialog, openChannels, openProfileEditor, openCsvImport, openMotionCalibration, openLightTest } from "./dialogs.js";
import { post, get } from "./api.js";
import { openBugReport } from "./bugreport.js";

let anchor = null;                 // last plain-clicked head, for shift ranges
let filterText = "";
let lastSig = "";

const ICONS = {
  moving: '<path d="M5 17h8M9 17v-3M5 7h8v6H5zM9 7V4"/>',
  par: '<circle cx="9" cy="9" r="5"/><circle cx="9" cy="9" r="2"/>',
  bar: '<rect x="2" y="7" width="14" height="4" rx="1"/><path d="M5 9h.01M9 9h.01M13 9h.01"/>',
  profile: '<path d="M3 7h6l5-3v10l-5-3H3z"/>',
  strobe: '<rect x="3" y="6" width="12" height="6" rx="1"/><path d="M5 9h8"/>',
  tube: '<path d="M9 2v14"/><circle cx="9" cy="16" r="1"/>',
  other: '<rect x="4" y="4" width="10" height="10" rx="2"/>',
};

function iconFor(body) {
  const t = (body && body.type) || "";
  const k = t.startsWith("moving") ? "moving"
    : t === "par" || t === "par_can" || t === "fresnel" || t === "wash_panel" || t === "cyc" ? "par"
      : t === "bar" || t === "matrix" ? "bar"
        : t === "profile" || t === "followspot" ? "profile"
          : t === "strobe" || t === "blinder" ? "strobe"
            : t === "tube" ? "tube" : "other";
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

function headRow(hd, child = false) {
  const b = hd.body || {};
  return h("tr" + (child ? ".fold-child" : "") + (parked().includes(hd.head_no) ? ".parked" : ""), { dataset: { head: hd.head_no } },
    h("td.c-no", h("i.lamp"), h("span", hd.head_no)),
    h("td.c-name", h("div.f-name",
      iconFor(b),
      h("div.f-text",
        h("b", { title: [hd.name || hd.model, ...[b.brand_name && b.brand_name !== "Generic" ? b.brand_name : hd.manufacturer, b.label, hd.mode]
          .filter(Boolean)].join(" · ") }, hd.name || hd.model),
        h("div.f-sub", [b.brand_name && b.brand_name !== "Generic" ? b.brand_name : hd.manufacturer, b.label, hd.mode]
          .filter(Boolean).join(" · "))),
      hd.unverified ? h("span.warn-ic", { title: "Generic channel layout - check it matches the real fixture" }, "⚠") : null,
      h("button.btn.ghost.small.icon.row-menu", { title: "Fixture actions", "aria-label": "Fixture actions" }, "⋯"))),
    h("td.c-addr", addr(hd)));
}

function foldRow(c) {
  const first = c.heads[0], lastHd = c.heads[c.heads.length - 1];
  const b = first.body || {};
  const open = expanded.has(first.head_no);
  return h("tr.fold" + (open ? ".open" : ""), { dataset: { fold: c.heads.map((x) => x.head_no).join(","), head: first.head_no } },
    h("td.c-no", h("button.fold-btn", { title: open ? "Fold" : "Show each light", "aria-label": open ? "Fold" : "Show each light",
        "aria-expanded": open ? "true" : "false" }, open ? "▾" : "▸"), h("span", `${first.head_no}–${lastHd.head_no}`)),
    h("td.c-name", h("div.f-name",
      iconFor(b),
      h("div.f-text",
        h("b", { title: [first.model, b.brand_name && b.brand_name !== "Generic" ? b.brand_name : first.manufacturer, b.label, first.mode]
          .filter(Boolean).join(" · ") }, first.model, h("em.f-qty", ` × ${c.heads.length}`)),
        h("div.f-sub", [b.brand_name && b.brand_name !== "Generic" ? b.brand_name : first.manufacturer, b.label, first.mode]
          .filter(Boolean).join(" · "))))),
    h("td.c-addr", h("span", addr(first)), h("small", "–" + addr(lastHd))));
}

function render() {
  const rows = visible();
  const sel = new Set(selected());
  const stale = new Set(((state.snap && state.snap.stale_heads) || []).map(Number));
  const tbody = $("#fx-rows");
  const sig = JSON.stringify([rows.map((r) => [r.head_no, r.name, r.universe, r.address, r.model, r.mode, r.body && r.body.type, r.unverified]),
    [...stale], [...expanded], parked()]);
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
  renderSelBar(sel);
  paintLamps();
}

// "6 selected · LED PARs" and, for two or more, the quick splits
function renderSelBar(sel) {
  const count = sel.size;
  const autos = (state.snap && state.snap.auto_groups) || [];
  const user = (state.snap && state.snap.groups) || [];
  const same = (heads) => heads.length === count && heads.every((x) => sel.has(x));
  const named = count ? ([...user].find((g) => same(g.heads)) || autos.find((g) => same(g.heads))) : null;
  $("#sel-count").textContent = count ? `${count} selected${named ? " · " + named.name : ""}` : "Nothing selected";
  const box = $("#sel-split");
  const key = count >= 2 ? "on" : "";
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(...(count >= 2 ? [["odd", "Odd"], ["even", "Even"], ["left", "Left"], ["right", "Right"]].map(([v, label]) =>
    h("button.chip", { title: `Keep the ${v} half of the selection`, onclick: () => run("select_split", { split: v }) }, label)) : []));
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

// Hold a group chip to flash that group (tap still selects it).
function holdToFlash(el, params) {
  let timer = 0, flashing = false;
  el.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.classList.contains("x")) return;
    timer = setTimeout(() => {
      flashing = true;
      el.classList.add("flashing");
      run("group_flash", { ...params, down: true }, { silentError: true });
    }, 350);
  });
  // swallows the click that follows a hold; armed only while that click
  // can still come, so a later real tap is never eaten
  const swallow = (e) => { e.stopImmediatePropagation(); e.preventDefault(); };
  const up = (e) => {
    clearTimeout(timer);
    if (!flashing) return;
    el.classList.remove("flashing");
    run("group_flash", { down: false }, { silentError: true });
    // released over the chip: a click follows, and it is not a tap.
    // Slid off or cancelled: no click comes, so nothing to swallow.
    if (e.type === "pointerup") {
      el.addEventListener("click", swallow, { capture: true, once: true });
      setTimeout(() => el.removeEventListener("click", swallow, { capture: true }), 0);
    }
    flashing = false;
  };
  el.addEventListener("pointerup", up);
  el.addEventListener("pointercancel", up);
  el.addEventListener("pointerleave", up);
  return el;
}

function renderGroups() {
  const box = $("#group-chips");
  const groups = (state.snap && state.snap.groups) || [];
  const autos = patch().length >= 4 ? ((state.snap && state.snap.auto_groups) || []) : [];
  const sel = new Set(selected());
  const same = (heads) => heads.length && heads.length === sel.size && heads.every((x) => sel.has(x));
  const key = JSON.stringify([groups, autos, [...sel]]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(
    ...autos.map((g) => holdToFlash(h("button.chip.auto" + (same(g.heads) ? ".on" : ""), {
      title: `Select ${g.name} (${g.heads.length}) · Shift adds to the selection · hold to flash`,
      onclick: (e) => run("select_group", { key: g.key, add: e.shiftKey || e.ctrlKey || e.metaKey }),
    }, g.kind === "rig" ? h("span.chip-ic", "⊢") : null, g.name, h("small", ` ${g.heads.length}`)), { auto: g.key })),
    ...groups.map((g) => holdToFlash(h("button.chip" + (same(g.heads) ? ".on" : ""), {
      title: `Select ${g.name} (${g.heads.length}) · hold to flash`,
      onclick: (e) => {
        if (e.target.classList.contains("x")) return;
        select(g.heads, { add: e.shiftKey || e.ctrlKey || e.metaKey });
      },
    }, g.name, h("small", ` ${g.heads.length}`), h("span.x", {
      title: "Delete group",
      onclick: async (e) => {
        e.stopPropagation();
        if (await confirmBox("Delete group", `Delete the group “${g.name}”? The fixtures stay patched.`, { ok: "Delete", danger: true })) {
          run("group_delete", { group: g.n });
        }
      },
    }, "×")), { group: g.n })));
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
    ...((hd.map || []).some((r) => r === "pan" || r === "tilt")
      ? [{ label: "Calibrate movement speed…", run: () => openMotionCalibration(hd) }] : []),
    { label: "Select all of this type", run: () => run("select_similar", { head }) },
    { label: "Frame on stage", run: () => window.jarvisStage && window.jarvisStage.frame([head]) },
    { label: "Report a problem with this light…", hint: "its file, DMX and 3D go with it", run: () => openBugReport(head) },
    "-",
    { label: "Remove from patch", danger: true, run: async () => {
      if (await confirmBox("Remove fixture", `Remove #${head} ${hd.name || hd.model} from the patch?\nCtrl+Z brings it back.`, { ok: "Remove", danger: true })) {
        run("remove_heads", { heads: [head] });
      }
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
async function openRdm() {
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

function toolsMenu(btn) {
  const sel = selected();
  menu(btn, [
    { label: "Add fixtures…", hint: "A", run: () => openAddDialog() },
    { label: "DMX map…", hint: clashes().length ? `${clashes().length} clash(es)` : "who uses which channels", run: openDmxMap },
    { label: "Scan the network for nodes", run: () => scanRig() },
    { label: "Ask the lights (RDM)…", hint: "model, mode and address from each light", run: () => openRdm() },
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
    "-",
    { label: "Remove selected fixtures", danger: true, disabled: !sel.length, run: async () => {
      if (await confirmBox("Remove fixtures", `Remove ${sel.length} fixture(s) from the patch? Ctrl+Z brings them back.`, { ok: "Remove", danger: true })) run("remove_heads", { heads: sel });
    } },
    { label: "Clear the whole patch", danger: true, run: async () => {
      if (await confirmBox("Clear patch", "Remove every fixture from the patch?\nCtrl+Z brings them back.", { ok: "Clear patch", danger: true })) post("/api/console/patch", { action: "clear" }).then(() => toast("Patch cleared"));
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

async function makeGroup() {
  const sel = selected();
  if (!sel.length) { toast("Select fixtures first, then make a group"); return; }
  const name = await promptBox("New group", `Name for these ${sel.length} fixture(s)`, "", { ok: "Create group", placeholder: "e.g. Back truss spots" });
  if (name) run("group_create", { name, heads: sel });
}

export function initFixtures() {
  $("#fx-rows").addEventListener("click", rowClick);
  $("#fx-rows").addEventListener("contextmenu", (e) => {
    const tr = e.target.closest("tr[data-head]");
    if (!tr || tr.dataset.fold) return;
    e.preventDefault();
    rowMenu(atPointer(e), +tr.dataset.head);
  });
  $("#fx-rows").addEventListener("dblclick", (e) => {
    const tr = e.target.closest("tr[data-head]");
    if (tr && window.jarvisStage) window.jarvisStage.frame([+tr.dataset.head]);
  });
  $("#fx-filter").addEventListener("input", (e) => { filterText = e.target.value; render(); });
  $("#fx-filter").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const heads = visible().map((x) => x.head_no);
      if (heads.length) select(heads);
    }
  });
  $("#add-btn").addEventListener("click", () => openAddDialog());
  $("#add-btn-2").addEventListener("click", () => openAddDialog());
  $("#fx-more").addEventListener("click", (e) => toolsMenu(e.currentTarget));
  $("#group-btn").addEventListener("click", makeGroup);
  for (const b of document.querySelectorAll("#sel-bar [data-act]")) {
    b.addEventListener("click", () => (b.dataset.act === "clear_selection" ? select([]) : run(b.dataset.act)));
  }
  on("snapshot", () => { render(); renderGroups(); renderClashes(); });
  on("selection", () => { render(); renderGroups(); });
  on("lite", render);
  on("looks", paintLamps);
  void get;
}

export { makeGroup };
