// The fixture list: what is patched, where, and what is selected.
import { state, on, patch, selected } from "./store.js";
import { run, select } from "./actions.js";
import { $, h, menu, promptBox, confirmBox, toast } from "./ui.js";
import { openAddDialog, openChannels, openProfileEditor, openCsvImport, openMotionCalibration, openLightTest } from "./dialogs.js";
import { post, get } from "./api.js";

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

function render() {
  const rows = visible();
  const sel = new Set(selected());
  const stale = new Set(((state.snap && state.snap.stale_heads) || []).map(Number));
  const tbody = $("#fx-rows");
  const sig = JSON.stringify([rows.map((r) => [r.head_no, r.name, r.universe, r.address, r.model, r.body && r.body.type, r.unverified]), [...stale]]);
  if (sig !== lastSig) {
    lastSig = sig;
    tbody.replaceChildren(...rows.map((hd) => {
      const b = hd.body || {};
      const tr = h("tr", { dataset: { head: hd.head_no } },
        h("td.c-no", hd.head_no),
        h("td", h("div.f-name",
          h("i.lamp"),
          iconFor(b),
          h("div.f-text",
            h("b", hd.name || hd.model),
            h("div.f-sub", [b.brand_name && b.brand_name !== "Generic" ? b.brand_name : hd.manufacturer, b.label, hd.mode]
              .filter(Boolean).join(" · "))),
          hd.unverified ? h("span.warn-ic", { title: "Generic channel layout - check it matches the real fixture" }, "⚠") : null,
          h("button.btn.ghost.small.icon.row-menu", { title: "Fixture actions", "aria-label": "Fixture actions" }, "⋯"))),
        h("td.c-addr", `${hd.universe}.${String(hd.address).padStart(3, "0")}`));
      return tr;
    }));
  }
  for (const tr of tbody.children) tr.classList.toggle("sel", sel.has(+tr.dataset.head));
  const n = patch().length;
  $("#fx-count").textContent = n;
  $(".fx-table-wrap").classList.toggle("is-empty", n === 0);
  const count = sel.size;
  $("#sel-count").textContent = count ? `${count} selected` : "Nothing selected";
  paintLamps();
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

function renderGroups() {
  const box = $("#group-chips");
  const groups = (state.snap && state.snap.groups) || [];
  box.replaceChildren(...groups.map((g) => h("button.chip", {
    title: `Select ${g.name} (${g.heads.length})`,
    onclick: (e) => {
      if (e.target.classList.contains("x")) return;
      select(g.heads, { add: e.shiftKey || e.ctrlKey || e.metaKey });
    },
  }, g.name, h("span.x", {
    title: "Delete group",
    onclick: async (e) => {
      e.stopPropagation();
      if (await confirmBox("Delete group", `Delete the group “${g.name}”? The fixtures stay patched.`, { ok: "Delete", danger: true })) {
        run("group_delete", { group: g.n });
      }
    },
  }, "×"))));
}

function rowClick(e) {
  const tr = e.target.closest("tr[data-head]");
  if (!tr) return;
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

function rowMenu(btn, head) {
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
    ...((hd.map || []).some((r) => r === "pan" || r === "tilt")
      ? [{ label: "Calibrate movement speed…", run: () => openMotionCalibration(hd) }] : []),
    { label: "Select all of this type", run: () => run("select_similar", { head }) },
    { label: "Frame on stage", run: () => window.jarvisStage && window.jarvisStage.frame([head]) },
    "-",
    { label: "Remove from patch", danger: true, run: async () => {
      if (await confirmBox("Remove fixture", `Remove #${head} ${hd.name || hd.model} from the patch?\nCtrl+Z brings it back.`, { ok: "Remove", danger: true })) {
        run("remove_heads", { heads: [head] });
      }
    } },
  ]);
}

function toolsMenu(btn) {
  const sel = selected();
  menu(btn, [
    { label: "Add fixtures…", hint: "A", run: () => openAddDialog() },
    { label: "Scan the network for nodes", run: () => scanRig() },
    { label: "Auto-address the patch", run: async () => {
      if (await confirmBox("Auto-address", "Re-address every fixture from 1.001 with no gaps or overlaps?\nCtrl+Z undoes it.", { ok: "Re-address" })) run("auto_patch", {}, { toast: true });
    } },
    "-",
    { label: "Export patch as CSV", run: () => exportCsv() },
    { label: "Import patch from CSV…", run: () => openCsvImport() },
    { label: "DMX channels of the selection", disabled: !sel.length, run: () => openChannels(sel) },
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
  on("snapshot", () => { render(); renderGroups(); });
  on("selection", render);
  on("lite", render);
  on("looks", paintLamps);
  void get;
}

export { makeGroup };
