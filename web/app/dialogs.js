// Dialogs: add fixtures, fixture profiles, DMX channels, CSV import, cues,
// shows, settings and help.
import { FixturePreview } from "/js/stage/stage.js";
import { get, post } from "./api.js";
import { state, patch, selected, outputState } from "./store.js";
import { run } from "./actions.js";
import { $, h, modal, toast, confirmBox, promptBox } from "./ui.js";

// ================================================================== add
let preview = null;

export function openAddDialog(query = "") {
  let chosen = null;
  let source = "all";
  const list = h("div.lib-list");
  const search = h("input", { type: "search", placeholder: "Search: brand, model, type…", value: query, autocomplete: "off" });
  const tabs = h("div.lib-tabs",
    h("button", { "aria-selected": "true", dataset: { src: "all" }, title: "Installed, the Jarvis library, Open Fixture Library, QLC+ and GDTF Share at once" }, "All"),
    h("button", { "aria-selected": "false", dataset: { src: "lib" } }, "Installed"),
    h("button", { "aria-selected": "false", dataset: { src: "open" }, title: "Jarvis library + Open Fixture Library + QLC+: thousands of lights, offline" }, "Libraries"),
    h("button", { "aria-selected": "false", dataset: { src: "share" } }, "GDTF Share"));
  const shareNote = h("div.muted.small", { style: { padding: "8px 12px" } });
  const pv = h("div.preview3d", h("div.cap"));
  const title = h("div.pick-title", "Pick a fixture");
  const meta = h("div.pick-meta", "One search covers everything: your installed fixtures, the Jarvis library, Open Fixture Library, QLC+ and (signed in) GDTF Share.");
  const mode = h("select.select", { style: { width: "100%" } });
  const qty = h("input", { type: "number", min: 1, max: 64, value: 1 });
  const uni = h("input", { type: "number", min: 1, placeholder: "auto" });
  const addr = h("input", { type: "number", min: 1, max: 512, placeholder: "auto" });
  const name = h("input", { type: "text", placeholder: "optional" });
  const addBtn = h("button.btn.primary", { disabled: true }, "Add to stage");
  const form = h("div.form-grid",
    h("label.field", { style: { gridColumn: "1 / -1" } }, h("span", "DMX mode"), mode),
    h("label.field", h("span", "Quantity"), qty),
    h("label.field", h("span", "Universe"), uni),
    h("label.field", h("span", "Address"), addr),
    h("label.field", h("span", "Name"), name));
  const right = h("div", pv, title, meta, form);
  const body = h("div.add-grid",
    h("div.lib", tabs, h("div.lib-search", search), shareNote, list), right);

  const close = modal({
    title: "Add fixtures", wide: true, body,
    foot: [h("span.muted.small.grow", "New fixtures are addressed after the last one and hung where that kind of light goes. Drag them on the stage to move them."),
      h("button.btn", { onclick: () => close() }, "Close"), addBtn],
    onClose: () => { if (preview) { preview.destroy(); preview = null; } },
  });

  preview = new FixturePreview(pv);

  function showPick(item) {
    chosen = item;
    [...list.children].forEach((b) => b.classList.toggle("on", b._item === item));
    const b = item.body || {};
    pv.querySelector(".cap").replaceChildren(h("b", b.label || "Fixture"), " · ", b.brand_name || item.manufacturer || "");
    if (preview && item.body) preview.show(item.body, `${item.manufacturer || ""} ${item.model || item.fixture || ""}`);
    title.textContent = `${item.manufacturer || ""} ${item.model || item.fixture || ""}`.trim();
    const modes = item.modes || [];
    mode.replaceChildren(...modes.map((m) => h("option", { value: m.name },
      `${m.name} (${m.channel_count ?? m.dmxfootprint ?? "?"} ch)`)));
    const origin = item._origin || source;
    if (origin === "share") {
      meta.textContent = `GDTF Share · revision ${item.revision || "?"} · downloads the manufacturer's file, then adds it`;
      addBtn.textContent = "Download and add";
    } else if (origin === "open") {
      meta.textContent = `${item.library} · ${modes.length} DMX mode(s) · community-made: check the mode against the light's manual`;
      addBtn.textContent = "Install and add";
    } else {
      meta.textContent = `${modes.length} DMX mode(s)` + (item.source ? ` · from ${item.source}` : "");
      addBtn.textContent = "Add to stage";
    }
    addBtn.disabled = false;
  }

  async function searchLib() {
    const q = search.value.trim();
    try {
      const d = await get("/api/fixtures?q=" + encodeURIComponent(q));
      const rows = d.results || [];
      list.replaceChildren(...rows.map((r) => {
        const b = h("button.lib-item", { onclick: () => showPick(r) },
          h("div.li-t", h("b", `${r.manufacturer} ${r.model}`),
            h("small", [(r.body && r.body.label) || "", `${(r.modes || []).length} mode(s)`].filter(Boolean).join(" · "))));
        b._item = r;
        return b;
      }));
      if (!rows.length) {
        list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } },
          q ? "Nothing installed matches. Try the Libraries or GDTF Share tab." : "The library is empty. Search the Libraries tab (thousands of lights, offline) or GDTF Share, or drop .gdtf / .qxf files into fixtures_inbox/ and import them from Settings."));
      } else if (!chosen) showPick(rows[0]);
    } catch (err) {
      list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, err.message));
    }
  }

  async function searchShare() {
    const st = await get("/api/gdtf/status").catch(() => ({}));
    if (!st.signed_in && !st.configured && !st.catalogue) {
      shareNote.replaceChildren(
        h("p", { style: { margin: "0 0 8px" } }, "Sign in with a free gdtf-share.com account. The password stays in this app's memory only."),
        shareLogin());
      list.replaceChildren();
      return;
    }
    shareNote.textContent = st.catalogue ? `${st.catalogue} fixtures in the catalogue` : "";
    const q = search.value.trim();
    if (q.length < 2) { list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, "Type at least two letters to search the Share.")); return; }
    list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, "Searching…"));
    const d = await get("/api/gdtf/search?limit=80&q=" + encodeURIComponent(q)).catch((e) => ({ error: e.message }));
    if (d.error) { list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, d.error)); return; }
    list.replaceChildren(...(d.results || []).map((r) => {
      const item = { ...r, model: r.fixture };
      const b = h("button.lib-item", { onclick: () => showPick(item) },
        h("div.li-t", h("b", `${r.manufacturer} ${r.fixture}`),
          h("small", [(r.body && r.body.label) || "", `rev ${r.revision || "?"}`, `${(r.modes || []).length} mode(s)`].join(" · "))));
      b._item = item;
      return b;
    }));
  }

  async function searchOpen() {
    const q = search.value.trim();
    if (q.length < 2) {
      list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, "Type a brand or model: the Open Fixture Library and QLC+ are searched offline."));
      return;
    }
    const d = await get("/api/fixtures/library?limit=80&q=" + encodeURIComponent(q)).catch((e) => ({ error: e.message }));
    if (d.error) { list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, d.error)); return; }
    const libs = d.libraries || [];
    shareNote.textContent = libs.map((l) => `${l.name}: ${l.fixtures} fixtures (${l.licence})`).join(" · ");
    const rows = d.results || [];
    if (!rows.length) {
      list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, "Nothing matches. Try fewer words, or the GDTF Share tab."));
      return;
    }
    list.replaceChildren(...rows.map((r) => {
      const item = { ...r, modes: (r.modes || []).map(([n, c]) => ({ name: n, channel_count: c })) };
      const b = h("button.lib-item", { onclick: () => showPick(item) },
        h("div.li-t", h("b", `${r.manufacturer} ${r.model}`),
          h("small", [r.src === "ofl" ? "OFL" : "QLC+", r.type || "", `${(r.modes || []).length} mode(s)`].filter(Boolean).join(" · "))));
      b._item = item;
      return b;
    }));
    if (!chosen) showPick(list.firstChild._item);
  }

  // One box, every source: installed first, then the Jarvis library,
  // OFL and QLC+ (offline), then GDTF Share when signed in.
  async function searchAll() {
    const q = search.value.trim();
    const tag = (text) => h("span.src-tag", text);
    const row = (item, name, bits, label) => {
      const b = h("button.lib-item", { onclick: () => showPick(item) },
        h("div.li-t", h("b", name, " ", tag(label)), h("small", bits.filter(Boolean).join(" · "))));
      b._item = item;
      return b;
    };
    shareNote.textContent = "";
    const [inst, lib] = await Promise.all([
      get("/api/fixtures?q=" + encodeURIComponent(q)).catch(() => ({})),
      q.length >= 2 ? get("/api/fixtures/library?limit=60&q=" + encodeURIComponent(q)).catch(() => ({})) : Promise.resolve({}),
    ]);
    const rows = [];
    for (const r of inst.results || []) {
      rows.push(row({ ...r, _origin: "lib" }, `${r.manufacturer} ${r.model}`,
        [(r.body && r.body.label) || "", `${(r.modes || []).length} mode(s)`], "installed"));
    }
    for (const r of lib.results || []) {
      const item = { ...r, _origin: "open", modes: (r.modes || []).map(([n, c]) => ({ name: n, channel_count: c })) };
      rows.push(row(item, `${r.manufacturer} ${r.model}`, [r.type || "", `${(r.modes || []).length} mode(s)`],
        r.src === "ofl" ? "OFL" : r.src === "qlc" ? "QLC+" : "Jarvis"));
    }
    list.replaceChildren(...rows);
    if (!rows.length) {
      list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } },
        q.length < 2 ? "Type a brand or model (e.g. \u201cfunfetti\u201d, \u201cwave 360\u201d)." : "Nothing matches in the installed fixtures or the libraries."));
    } else if (!chosen) showPick(rows[0]._item);
    if (q.length < 2) return;
    const st = await get("/api/gdtf/status").catch(() => ({}));
    if (!(st.signed_in || st.catalogue) || search.value.trim() !== q) return;
    const d = await get("/api/gdtf/search?limit=30&q=" + encodeURIComponent(q)).catch(() => ({}));
    if (search.value.trim() !== q || source !== "all") return;
    for (const r of d.results || []) {
      list.append(row({ ...r, model: r.fixture, _origin: "share" }, `${r.manufacturer} ${r.fixture}`,
        [(r.body && r.body.label) || "", `rev ${r.revision || "?"}`], "GDTF Share"));
    }
  }

  function shareLogin() {
    const user = h("input", { type: "text", placeholder: "user", autocomplete: "username" });
    const pass = h("input", { type: "password", placeholder: "password", autocomplete: "current-password" });
    const go = h("button.btn.primary.small", {
      onclick: async () => {
        const d = await post("/api/gdtf/login", { user: user.value, password: pass.value }).catch((e) => ({ error: e.message }));
        if (d.error) toast(d.error, "bad");
        else { toast("Signed in to GDTF Share", "ok"); searchShare(); }
      },
    }, "Sign in");
    return h("div.row-btns", user, pass, go);
  }

  let t = 0;
  search.addEventListener("input", () => {
    clearTimeout(t);
    t = setTimeout(() => (source === "all" ? searchAll() : source === "lib" ? searchLib() : source === "open" ? searchOpen() : searchShare()), source === "lib" ? 120 : 300);
  });
  tabs.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    source = b.dataset.src;
    [...tabs.children].forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    chosen = null;
    addBtn.disabled = true;
    shareNote.replaceChildren();
    if (source === "all") searchAll(); else if (source === "lib") searchLib(); else if (source === "open") searchOpen(); else searchShare();
  });

  addBtn.addEventListener("click", async () => {
    if (!chosen) return;
    addBtn.disabled = true;
    try {
      let item = chosen;
      const origin = chosen._origin || source;
      if (origin === "share") {
        const d = await post("/api/gdtf/download", { rid: chosen.rid });
        if (d.error) throw new Error(d.error);
        toast(d.summary || "Downloaded", "ok");
        const found = await get("/api/fixtures?q=" + encodeURIComponent(`${d.manufacturer} ${d.model}`));
        item = (found.results || [])[0];
        if (!item) throw new Error("downloaded, but it did not appear in the library");
      } else if (origin === "open") {
        const d = await post("/api/fixtures/library/install", { src: chosen.src, key: chosen.key });
        if (d.error || !d.fixture) throw new Error(d.error || "the fixture could not be installed");
        toast(d.summary || "Installed", "ok");
        item = d.fixture;
      }
      const params = { qty: Math.max(1, Math.min(64, +qty.value || 1)) };
      if (item.id) params.fixture_id = item.id; else params.query = `${item.manufacturer} ${item.model}`;
      if (mode.value) params.mode = mode.value;
      if (uni.value) params.universe = +uni.value;
      if (addr.value) params.address = +addr.value;
      if (name.value.trim()) params.name = name.value.trim();
      const r = await run("add_heads", params);
      if (r.ok) toast(r.summary || "Added", "ok");
    } catch (err) {
      toast(err.message, "bad");
    } finally {
      addBtn.disabled = false;
    }
  });
  searchAll();
  return close;
}

// ========================================================== channels
export async function openChannels(heads) {
  const d = await get("/api/console/channels?heads=" + heads.join(",")).catch((e) => ({ error: e.message }));
  if (d.error) { toast(d.error, "bad"); return; }
  const body = h("div", ...(d.heads || []).map((hd) => h("div", { style: { marginBottom: "18px" } },
    h("h3", `#${hd.head_no} ${hd.name} · U${hd.universe}.${hd.address} · ${hd.mode}`),
    h("table.chan-table",
      h("thead", h("tr", h("th", "Ch"), h("th", "DMX"), h("th", "Label"), h("th", "Role"), h("th", "Value"))),
      h("tbody", ...(hd.channels || []).map((c) => h("tr",
        h("td.mono", c.n), h("td.mono", `${hd.universe}.${c.abs}`), h("td", c.label),
        h("td" + (c.role === "raw" ? ".raw" : ""), c.role), h("td.mono", c.value))))))));
  modal({ title: "DMX channels", body, wide: true, foot: [h("span.muted.small.grow", d.summary || "")] });
}

// =================================================== fixture profile
export async function openProfileEditor(hd) {
  const found = await get("/api/fixtures?q=" + encodeURIComponent(`${hd.manufacturer} ${hd.model}`)).catch(() => ({}));
  const item = (found.results || []).find((r) => r.manufacturer === hd.manufacturer && r.model === hd.model);
  if (!item) { toast("This fixture's profile is not in the library (a generic stand-in)", "bad"); return; }
  const prof = await get("/api/fixtures/profile?id=" + item.id).catch((e) => ({ error: e.message }));
  if (prof.error) { toast(prof.error, "bad"); return; }
  const m = (prof.modes || []).find((x) => x.name === hd.mode) || (prof.modes || [])[0];
  if (!m) return;
  const rows = m.channels.map((label, i) => {
    const role = m.roles[i] || {};
    const input = h("input", { type: "text", value: label, style: { width: "100%" } });
    input.addEventListener("change", async () => {
      const d = await post("/api/fixtures/channel", { mode_id: m.id, channel: i + 1, label: input.value }).catch((e) => ({ error: e.message }));
      if (d.error) toast(d.error, "bad"); else toast(`Channel ${i + 1} is now ${d.role || input.value}`, "ok");
    });
    return h("tr", h("td.mono", i + 1), h("td", input),
      h("td" + (role.controllable ? "" : ".raw"), role.role || ""),
      h("td.mono", role.min ?? ""), h("td.mono", role.max ?? ""));
  });
  modal({
    title: `${prof.manufacturer} ${prof.model} · ${m.name}`, wide: true,
    body: h("div",
      h("p.muted.small", "Rename a channel to give it a control - e.g. call a channel “Zoom” and the programmer can drive it. Roles shown in amber have no control yet."),
      h("table.chan-table", h("thead", h("tr", h("th", "Ch"), h("th", "Label"), h("th", "Role"), h("th", "Min"), h("th", "Max"))), h("tbody", ...rows))),
  });
}

// ================================================================ CSV
export function openCsvImport() {
  const ta = h("textarea", { rows: 10, placeholder: "head,name,manufacturer,model,mode,universe,address\n1,Spot 1,Chauvet,Rogue R2 Spot,16ch,1,1", spellcheck: "false" });
  const file = h("input", { type: "file", accept: ".csv,text/csv" });
  file.addEventListener("change", async () => { if (file.files[0]) ta.value = await file.files[0].text(); });
  const close = modal({
    title: "Import patch from CSV", wide: true,
    body: h("div", h("p.muted.small", "Replaces the patch with the rows below (Ctrl+Z undoes it). Columns follow the exported patch sheet."), file, h("div", { style: { height: "8px" } }), ta),
    foot: [h("button.btn", { onclick: () => close() }, "Cancel"), h("button.btn.primary", {
      onclick: async () => {
        const d = await post("/api/console/patch", { action: "from_csv", csv: ta.value }).catch((e) => ({ error: e.message }));
        const r = d.result || {};
        if (d.error || !r.ok) toast(d.error || r.error || "import failed", "bad");
        else { toast(r.summary || "Patch imported", "ok"); close(); }
      },
    }, "Import")],
  });
}

// ================================================================ cues
export function openCueDialog(playback) {
  const pbs = (state.snap && state.snap.playbacks) || [];
  const pbSel = h("select.select", ...pbs.map((p) => h("option", { value: p.n },
    `PB${p.n}${p.name ? " " + p.name : ""} (${(p.stack || []).length} cues)`)));
  pbSel.value = String(playback || 1);
  const name = h("input", { type: "text", placeholder: "e.g. Verse 1" });
  const fade = h("input", { type: "number", min: 0, step: 0.5, value: 2 });
  const hold = h("input", { type: "number", min: 0, step: 0.5, placeholder: "none" });
  const close = modal({
    title: "Record cue",
    body: h("div.form-grid",
      h("label.field", h("span", "Playback"), pbSel), h("label.field", h("span", "Name"), name),
      h("label.field", h("span", "Fade (s)"), fade), h("label.field", h("span", "Hold before auto-follow (s)"), hold)),
    foot: [h("span.muted.small.grow", "Records what the programmer holds, then clears it."),
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", {
        onclick: async () => {
          const params = { playback: +pbSel.value, name: name.value.trim(), fade: +fade.value || 0 };
          if (hold.value !== "") params.hold = +hold.value;
          const r = await run("record_cue", params, { toast: true });
          if (r.ok) close();
        },
      }, "Record")],
  });
}

// A cue's follow has three states: null inherits the stack delay, 0 waits
// for GO, a positive number auto-advances after that many seconds.
function followState(c) {
  if (c.follow_s === null || c.follow_s === undefined) return "inherit";
  return c.follow_s > 0 ? "auto" : "wait";
}

// Fade, hold and auto-follow wait as proportional segments, scaled to the
// longest cue in the stack so short and long shows both read.
function cueTimeline(c, longest) {
  const f = Math.max(0, +(c.fade_s || 0));
  const hd = Math.max(0, +(c.hold_s || 0));
  const mode = followState(c);
  const w = mode === "auto" ? c.follow_s : 0;
  const seg = (cls, secs, title) => secs > 0
    ? h("i." + cls, { title, style: { width: (secs / longest) * 100 + "%" } }) : null;
  const how = mode === "auto" ? `auto after ${w}s` : mode === "wait" ? "waits for GO" : "inherits the stack";
  return h("div.cue-tl." + mode, { title: `fade ${f}s · hold ${hd}s · ${how}` },
    seg("seg-fade", f, `fade ${f}s`), seg("seg-hold", hd, `hold ${hd}s`), seg("seg-follow", w, how));
}

function followCell(n, c, refresh) {
  const mode = followState(c);
  const sel = h("select.select.small", { title: "What happens after this cue" },
    h("option", { value: "inherit" }, "Stack"), h("option", { value: "wait" }, "Wait"),
    h("option", { value: "auto" }, "Auto"));
  sel.value = mode;
  const secs = h("input.num", { type: "number", min: 0, step: 0.5, title: "Seconds before the next cue runs",
    value: mode === "auto" ? c.follow_s : "", placeholder: "s", disabled: mode !== "auto" });
  const send = () => {
    const follow = sel.value === "inherit" ? null : sel.value === "wait" ? 0 : Math.max(0.1, +secs.value || 2);
    run("edit_cue", { playback: n, cue: c.n, follow }).then(refresh);
  };
  sel.addEventListener("change", () => {
    secs.disabled = sel.value !== "auto";
    if (sel.value === "auto" && !secs.value) secs.value = 2;
    send();
  });
  secs.addEventListener("change", send);
  return h("div.row-btns", sel, secs);
}

export function openCueList(n) {
  let close = null;
  const render = () => {
    const pb = ((state.snap && state.snap.playbacks) || []).find((p) => p.n === n) || { stack: [] };
    const stack = pb.stack || [];
    const longest = Math.max(1, ...stack.map((c) =>
      (+c.fade_s || 0) + (+c.hold_s || 0) + (c.follow_s > 0 ? c.follow_s : 0)));
    const rows = stack.map((c, i) => {
      const nm = h("input", { type: "text", value: c.name || "", placeholder: `Cue ${c.n}` });
      const fd = h("input.num", { type: "number", min: 0, step: 0.5, value: c.fade_s ?? 0 });
      const hd = h("input.num", { type: "number", min: 0, step: 0.5, value: c.hold_s ?? 0 });
      nm.addEventListener("change", () => run("rename_cue", { playback: n, cue: c.n, name: nm.value }));
      fd.addEventListener("change", () => run("edit_cue", { playback: n, cue: c.n, fade: +fd.value }).then(refresh));
      hd.addEventListener("change", () => run("edit_cue", { playback: n, cue: c.n, hold: +hd.value }).then(refresh));
      return h("tr" + (i === pb.index ? ".sel" : ""),
        h("td.mono", c.n), h("td", nm, cueTimeline(c, longest)), h("td", fd), h("td", hd),
        h("td", followCell(n, c, () => refresh())),
        h("td", h("div.row-btns",
          h("button.btn.small", { title: "Go to this cue", onclick: () => run("cue_go", { playback: n, cue: c.n }) }, "Go"),
          h("button.btn.small.ghost", { title: "Move up", disabled: i === 0, onclick: () => run("move_cue", { playback: n, cue: c.n, to: c.n - 1 }).then(refresh) }, "↑"),
          h("button.btn.small.ghost", { title: "Move down", disabled: i === stack.length - 1, onclick: () => run("move_cue", { playback: n, cue: c.n, to: c.n + 1 }).then(refresh) }, "↓"),
          h("button.btn.small.ghost", { title: "Update this cue from the programmer", onclick: () => run("record_cue", { playback: n, cue: c.n }, { toast: true }) }, "Update"),
          h("button.btn.small.ghost", { title: "Insert an empty cue below", onclick: () => run("insert_cue", { playback: n, at: c.n + 1 }).then(refresh) }, "+"),
          h("button.btn.small.ghost", { title: "Delete", onclick: () => run("delete_cue", { playback: n, cue: c.n }).then(refresh) }, "×"))));
    });
    const delay = (pb.follow || {}).delay;
    return h("div",
      h("table.chan-table", h("thead", h("tr", h("th", "#"), h("th", "Name"), h("th", "Fade s"), h("th", "Hold s"),
        h("th", { title: "Stack inherits the playback's auto-follow; Wait holds for GO; Auto runs the next cue by itself" }, "Then"), h("th", ""))),
        h("tbody", ...(rows.length ? rows : [h("tr", h("td", { colspan: 6, class: "muted" }, "No cues yet - set a look and record one."))]))),
      h("p.muted.small", "Then: ", h("b", "Stack"), ` follows the playback's auto-follow${delay ? ` (${delay}s)` : ""}, `,
        h("b", "Wait"), " holds for GO, ", h("b", "Auto"), " runs the next cue after the seconds given."));
  };
  const body = h("div", render());
  const refresh = () => setTimeout(() => body.replaceChildren(render()), 300);
  close = modal({
    title: `Playback ${n} · cue list`, wide: true, body,
    foot: [h("button.btn", { onclick: () => openCueDialog(n) }, "Record new cue"),
      h("span.grow"), h("button.btn", { onclick: () => close() }, "Done")],
  });
}

// ================================================================ shows
export async function openShowMenu(anchor, menuFn) {
  const shows = (state.snap && state.snap.shows) || [];
  const current = (state.snap && state.snap.show_file) || "";
  menuFn(anchor, [
    { label: "Save", hint: "Ctrl+S", run: () => saveShow(current) },
    { label: "Save as…", run: () => saveShow("") },
    "-",
    ...shows.slice(0, 14).map((name) => ({
      label: "Open " + name + (name === current ? "  (open)" : ""),
      run: async () => {
        if (await confirmBox("Open show", `Open “${name}”? The current patch, cues and palettes are replaced (the autosave keeps what you had until the next change).`, { ok: "Open" })) {
          const d = await post("/api/console/load", { name }).catch((e) => ({ error: e.message }));
          const r = d.result || {};
          if (d.error || !r.ok) toast(d.error || r.error, "bad"); else toast(r.summary || "Opened", "ok");
        }
      },
    })),
    shows.length ? null : { label: "No saved shows yet", disabled: true, run() {} },
  ]);
}

export async function saveShow(name) {
  let n = name;
  if (!n) n = await promptBox("Save show", "Show name (letters, digits, dash, underscore)", "", { ok: "Save" });
  if (!n) return;
  const d = await post("/api/console/save", { name: n }).catch((e) => ({ error: e.message }));
  const r = d.result || {};
  if (d.error || !r.ok) toast(d.error || r.error, "bad"); else toast(r.summary || "Saved", "ok");
}

// ============================================================== settings
const VENUE_TEMPLATES = [["club", "Club"], ["small_club", "Small club / bar"], ["warehouse", "Warehouse rave"],
  ["concert", "Concert stage"], ["theatre", "Theatre"], ["ballroom", "Ballroom / event"], ["outdoor", "Outdoor stage"]];
// ---- Settings -> Output: where the DMX goes at this venue -------------
// Every venue's lighting network is different, so the node is chosen
// here and saved with the show: Auto (this computer's lighting network),
// one node's IP, or a broadcast address.  Find nodes polls every adapter.
function outputSection(con) {
  const box = h("div.out-box");
  const mode = h("select.select",
    h("option", { value: "auto" }, "Auto: broadcast on my lighting network"),
    h("option", { value: "node" }, "One node: send to its IP"),
    h("option", { value: "broadcast" }, "Broadcast address I choose"));
  const ip = h("input", { type: "text", inputmode: "decimal", placeholder: "e.g. 2.0.0.10", spellcheck: "false", autocomplete: "off" });
  const proto = h("select.select",
    h("option", { value: "" }, `Default (${(con.transport || "artnet") === "sacn" ? "sACN" : "Art-Net"})`),
    h("option", { value: "artnet" }, "Art-Net"),
    h("option", { value: "sacn" }, "sACN (E1.31)"));
  const status = h("div.out-status");
  const adapters = h("div.out-list");
  const nodes = h("div.out-list");
  const syncIp = () => { ip.disabled = mode.value === "auto"; ip.parentElement.style.opacity = ip.disabled ? 0.5 : 1; };
  mode.addEventListener("change", syncIp);

  const verdict = (net) => {
    const r = net.resolved || {};
    const lines = [h("div", h("b", "Sending to "), h("span.mono", `${r.host}:${r.port}`), ` · ${r.transport === "sacn" ? "sACN" : "Art-Net"}`,
      net.target.mode === "auto" && net.env_host ? " (from DMX_HOST in .env)" : "",
      ` · ${con.hz || 40} Hz`)];
    const c = net.check;
    const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
    if (c) lines.push(h(c.ok ? "div.out-ok" : "div.out-bad", `${c.ok ? "✓" : "⚠"} ${cap(c.message)}`));
    else if (r.host === "255.255.255.255") lines.push(h("div.out-bad", "⚠ Global broadcast: it leaves by one network only. Pick the node or connect to the lighting network."));
    status.replaceChildren(...lines);
  };
  const showAdapters = (net) => {
    const rows = net.interfaces || [];
    adapters.replaceChildren(h("div.muted.small", "This computer's networks"),
      ...(rows.length ? rows.map((i) => h("div.out-row",
        h("span.mono", `${i.ip}${i.mask ? " / " + i.mask : ""}`),
        h("span.muted.small", i.name || ""),
        i.ip.startsWith("2.") ? h("span.out-tag", "Art-Net range") : null))
        : [h("div.out-bad", "No network found. Plug in the lighting network cable.")]));
  };
  const refresh = async () => {
    try {
      const net = await get("/api/console/network");
      mode.value = net.target.mode;
      ip.value = net.target.host || "";
      proto.value = net.target.transport || "";
      syncIp(); verdict(net); showAdapters(net);
      return net;
    } catch (err) {
      status.replaceChildren(h("div.out-bad", err.message));
      return null;
    }
  };
  const apply = async (params) => {
    const res = await run("set_dmx_target", params, { toast: true });
    if (res.ok) await refresh();
  };
  const find = async (btn) => {
    btn.disabled = true;
    nodes.replaceChildren(h("div.muted.small", "Asking every network for Art-Net nodes…"));
    try {
      const d = await post("/api/console/scan", { import: false });
      const r = d.result || {};
      const found = r.nodes || [];
      if (!found.length) {
        nodes.replaceChildren(h("div.out-bad", r.message || r.scan_error || "No node answered."),
          h("div.muted.small", "Some nodes don't answer polls. If you know the node's IP (it is often on a label), type it above and choose \u201cOne node\u201d."));
        return;
      }
      nodes.replaceChildren(h("div.muted.small", `${found.length} node(s) answered`),
        ...found.map((n) => h("div.out-row",
          h("span.mono", n.ip),
          h("span", n.long_name || n.name || "Art-Net node"),
          h("span.muted.small", (n.output_ports || []).length ? `universes ${n.output_ports.map((pa) => pa + 1).join(", ")}` : ""),
          h("button.btn.small", { onclick: () => apply({ mode: "node", host: n.ip }) }, "Use this node"))));
    } catch (err) {
      nodes.replaceChildren(h("div.out-bad", err.message));
    } finally {
      btn.disabled = false;
    }
  };
  box.append(
    h("div.form-grid",
      h("label.field", h("span", "Send DMX to"), mode),
      h("label.field", h("span", "Node / broadcast IP"), ip),
      h("label.field", h("span", "Protocol"), proto)),
    h("div.row-btns",
      h("button.btn.primary", { onclick: () => apply({ mode: mode.value, host: mode.value === "auto" ? "" : ip.value.trim(), transport: proto.value }) }, "Apply"),
      h("button.btn", { onclick: (e) => find(e.currentTarget) }, "Find nodes"),
      h("button.btn", { onclick: refresh }, "Re-check network")),
    status, nodes, adapters,
    h("p.muted.small", "Saved with the show, so each venue keeps its own node. Changes apply straight away, no restart."));
  ip.addEventListener("keydown", (e) => { if (e.key === "Enter") apply({ mode: mode.value === "auto" ? "node" : mode.value, host: ip.value.trim(), transport: proto.value }); });
  status.append(h("div.muted.small", "Checking the network…"));
  refresh();
  return box;
}

export async function openSettings() {
  const status = await get("/api/status").catch(() => ({}));
  const con = status.console || {};
  const v = (state.snap && state.snap.venue) || {};
  const room = v.room || {};
  const auto = v.auto || !room.width;
  const w = h("input", { type: "number", min: 4, step: 0.5, value: auto ? "" : room.width, placeholder: "auto" });
  const d = h("input", { type: "number", min: 4, step: 0.5, value: auto ? "" : room.depth, placeholder: "auto" });
  const ht = h("input", { type: "number", min: 2.2, step: 0.1, value: auto ? "" : room.height, placeholder: "auto" });
  const tpl = h("select.select", ...VENUE_TEMPLATES.map(([k, label]) => h("option", { value: k }, label)));
  tpl.value = v.template || "club";
  const quality = h("select.select",
    h("option", { value: "auto" }, "Auto (adapts to this computer)"),
    h("option", { value: "high" }, "High (sharpest, needs a good GPU)"),
    h("option", { value: "fast" }, "Fast (older laptops)"));
  try { quality.value = localStorage.getItem("jarvis.quality") || "auto"; } catch (e) { /* ignore */ }
  quality.addEventListener("change", () => {
    try { localStorage.setItem("jarvis.quality", quality.value); } catch (e) { /* ignore */ }
    import("./stagepanel.js").then((m) => { const st = m.getStage(); if (st) st.setOptions({ quality: quality.value }); });
  });
  const midi = status.midi || {};
  const body = h("div",
    h("h3", "Venue"),
    h("p.muted.small", auto ? "No room drawn yet: the 3D view sizes one around your lights."
      : `${v.name || "Room"}: ${room.width} × ${room.depth} m, ${room.height} m ceiling · ${(v.rigging || []).length} rigging · ${(v.zones || []).length} zones`),
    h("div.form-grid",
      h("label.field", h("span", "Start from"), tpl),
      h("div.field", h("span", " "), h("button.btn", {
        onclick: async () => {
          if (!(await confirmBox("Replace the venue", "Start from this template? The room, rigging and zones are replaced; your lights stay where they are (Ctrl+Z undoes it).", { ok: "Replace" }))) return;
          run("venue_template", { name: tpl.value, width: +w.value || null, depth: +d.value || null, height: +ht.value || null }, { toast: true });
        },
      }, "Use template"))),
    h("div.form-grid",
      h("label.field", h("span", "Width m"), w), h("label.field", h("span", "Depth m"), d), h("label.field", h("span", "Ceiling m"), ht),
      h("div.field", h("span", " "), h("button.btn", {
        onclick: () => run("venue_room", { width: +w.value || null, depth: +d.value || null, height: +ht.value || null }, { toast: true }),
      }, "Resize room"))),
    h("h3", "3D view"),
    h("div.form-grid", h("label.field", h("span", "Quality"), quality)),
    h("h3", "Output"),
    outputSection(con),
    h("h3", "Fixture library"),
    h("div.row-btns",
      h("span.muted.small", `${status.fixtures ?? "?"} fixture types installed.`),
      h("button.btn", {
        onclick: async () => {
          const r = await post("/api/fixtures/import", {}).catch((e) => ({ error: e.message }));
          if (r.error) toast(r.error, "bad");
          else toast(`Imported ${(r.imported || []).length} file(s) from fixtures_inbox/` + ((r.errors || []).length ? ` · ${(r.errors || []).length} failed` : ""), (r.errors || []).length ? "bad" : "ok");
        },
      }, "Import fixture files from fixtures_inbox/ (.gdtf, .qxf, OFL .json)")),
    h("h3", "MIDI"),
    h("p.muted.small", midi.enabled ? (midi.open ? `Listening to ${midi.device}` : (midi.error || "No MIDI device found")) : "MIDI is off (MIDI_ENABLED=false)."),
    h("h3", "AI"),
    h("p.muted.small", status.llm_configured ? `Using ${status.model}` : "No AI key: the copilot uses its offline compiler. Add LLM_API_KEY to .env for the full copilot."));
  modal({ title: "Settings", body, wide: false });
}

// ================================================================== help
const KEYS = [
  ["Space / Enter", "GO on the focused playback"], ["B", "back one cue"], ["X", "blackout"],
  ["/ or Ctrl+K", "command bar"], ["Ctrl+Z / Ctrl+Shift+Z", "undo / redo"], ["Ctrl+S", "save the show"],
  ["A / Shift+A", "select all / none"], ["1 … 9", "select fixture 1–9 (Shift adds)"], ["G", "group the selection"],
  ["L", "locate"], ["C", "clear the programmer"], ["R", "record a cue"], ["O", "overwrite the current cue"], ["I", "insert a cue after the current one"], ["D", "delete the current cue"], ["↑ ↓", "intensity ±5 (Shift ±1)"],
  ["F", "frame the selection on stage"], ["Esc", "close / leave full screen"], ["?", "this help"],
];
const SYNTAX = [
  ["1-4 red", "select 1 to 4, colour red"], ["1.3.5 dimmer 70", "select 1, 3 and 5, dimmer 70"], ["all warm white", "every fixture warm white"],
  ["1-4 pan 90", "pan 90° (through each fixture's own range)"], ["group 2 zoom 200", "a group, raw value"],
  ["cue 3 go", "go to cue 3"], ["record", "record a cue"], ["master 60", "grand master"], ["blackout on", "blackout"],
];

export function openHelp() {
  const dl = (rows) => h("dl", ...rows.flatMap(([k, v]) => [h("dt", h("kbd", k)), h("dd", v)]));
  modal({
    title: "Jarvis help", wide: true,
    body: h("div.help-grid",
      h("div", h("h3", "Keys"), dl(KEYS)),
      h("div", h("h3", "Command bar syntax"), dl(SYNTAX),
        h("p.muted.small", "Anything that is not console syntax - “slow blue wash on the movers” - goes to the copilot, which shows you the plan before it touches the rig.")),
      h("div", h("h3", "Workflow"),
        h("ol", { style: { paddingLeft: "18px", color: "var(--text-2)", lineHeight: 1.6, margin: 0 } },
          h("li", "Add fixtures - they appear on the stage straight away."),
          h("li", "Drag them to where they hang (Shift+drag for height)."),
          h("li", "Select, then set intensity, colour, position, beam."),
          h("li", "Record cues onto a playback and press GO."),
          h("li", "When the rig is connected: Go live.")))),
  });
}

export { patch, selected };

// ===================================================== movement speed
// Time the REAL light, so the visualiser moves it at the same pace: the
// light sweeps pan (then tilt) end to end at top speed and you tap when
// it stops.  Saved per fixture model, so every head of it matches.
const REACTION_S = 0.2;          // a tap lands about this late
export async function openMotionCalibration(hd) {
  const head = hd.head_no;
  const got = await run("motion_get", { head }, { silentError: true });
  if (!got.ok) { toast(got.error || "This fixture cannot be calibrated", "bad"); return; }
  const axes = got.axes || [];
  if (!axes.length) { toast("This fixture has no pan or tilt", "bad"); return; }
  const taps = { pan: [], tilt: [] };
  const inputs = {};
  let t0 = 0;
  let axis = axes[0];
  let phase = "idle";                       // idle -> ready -> timing
  const live = outputState() === "live";
  const stepText = h("div.cal-step");
  const big = h("button.btn.primary.cal-big");
  const result = h("div.muted.small");
  const fields = h("div.form-grid", ...axes.map((ax) => {
    inputs[ax] = h("input", { type: "number", min: 0.2, max: 60, step: 0.1,
      value: got.motion && got.motion[ax + "_s"] ? got.motion[ax + "_s"] : "", placeholder: "default" });
    return h("label.field", h("span", `Full ${ax}, seconds`), inputs[ax]);
  }));
  const axisPick = h("div.row-btns", ...axes.map((ax) => h("button.btn.small", {
    dataset: { ax }, onclick: () => { axis = ax; reset(); },
  }, `Time ${ax}`)));

  const avg = (list) => list.reduce((a, b) => a + b, 0) / list.length;
  function render() {
    [...axisPick.children].forEach((b) => b.classList.toggle("on", b.dataset.ax === axis));
    if (phase === "idle") {
      stepText.textContent = `1. Send the head to the start of its ${axis}. Wait until the REAL light has stopped moving.`;
      big.textContent = `Move to ${axis} start`;
    } else if (phase === "ready") {
      stepText.textContent = `2. Press Go and watch the real light: it sweeps the whole ${axis} at top speed.`;
      big.textContent = "Go";
    } else {
      stepText.textContent = "3. Tap the moment the real light STOPS.";
      big.textContent = "It stopped!";
    }
    const n = taps[axis].length;
    result.textContent = n ? `${axis}: ${taps[axis].map((x) => x.toFixed(2)).join(" s, ")} s → using ${avg(taps[axis]).toFixed(2)} s` +
      (n < 2 ? " (time it twice for a better average)" : "") : "";
  }
  function reset() { phase = "idle"; render(); }
  big.addEventListener("click", async () => {
    if (phase === "idle") {
      const r = await run("motion_test", { head, axis, to: "start" });
      if (r.ok) { phase = "ready"; render(); }
    } else if (phase === "ready") {
      phase = "timing";
      render();
      t0 = performance.now();
      const r = await run("motion_test", { head, axis, to: "end" });
      if (!r.ok) reset();
    } else {
      const s = Math.max(0.2, (performance.now() - t0) / 1000 - REACTION_S);
      taps[axis].push(s);
      inputs[axis].value = avg(taps[axis]).toFixed(2);
      reset();
    }
  });
  const body = h("div.cal",
    h("p", `Make the 3D view move #${head} ${hd.name || hd.model} as fast as the real light. The result is saved for every ${hd.model}.`),
    live ? null : h("p.out-bad", "The output is not live, so the real light will not move. Press Go live first, or type the times below from a stopwatch."),
    axisPick, stepText, big, result, fields,
    h("p.muted.small", "Tip: time each axis twice. The speed channel is set to fastest for the test, and the head gets back exactly what it was doing when you close this."));
  const close = modal({
    title: "Calibrate movement speed", body,
    foot: [
      h("button.btn", { onclick: async () => { await run("motion_set", { head, clear: true }, { toast: true }); close(); } }, "Back to defaults"),
      h("span.grow"),
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", { onclick: async () => {
        const params = { head };
        for (const ax of axes) if (inputs[ax].value) params[ax + "_s"] = +inputs[ax].value;
        const r = await run("motion_set", params, { toast: true });
        if (r.ok) close();
      } }, "Save"),
    ],
    onClose: () => run("motion_test_end", { head }, { silentError: true }),
  });
  render();
}
