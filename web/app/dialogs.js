// Dialogs: add fixtures, fixture profiles, DMX channels, CSV import, cues,
// shows, settings and help.
import { FixturePreview } from "/js/stage/stage.js";
import { get, post } from "./api.js";
import { state, patch, selected } from "./store.js";
import { run } from "./actions.js";
import { $, h, modal, toast, confirmBox, promptBox } from "./ui.js";

// ================================================================== add
let preview = null;

export function openAddDialog(query = "") {
  let chosen = null;
  let source = "lib";
  const list = h("div.lib-list");
  const search = h("input", { type: "search", placeholder: "Search: brand, model, type…", value: query, autocomplete: "off" });
  const tabs = h("div.lib-tabs",
    h("button", { "aria-selected": "true", dataset: { src: "lib" } }, "Installed"),
    h("button", { "aria-selected": "false", dataset: { src: "share" } }, "GDTF Share"));
  const shareNote = h("div.muted.small", { style: { padding: "8px 12px" } });
  const pv = h("div.preview3d", h("div.cap"));
  const title = h("div.pick-title", "Pick a fixture");
  const meta = h("div.pick-meta", "Everything installed is listed on the left; the GDTF Share tab has thousands more, straight from the manufacturers.");
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
    if (source === "share") {
      meta.textContent = `GDTF Share · revision ${item.revision || "?"} · downloads the manufacturer's file, then adds it`;
      addBtn.textContent = "Download and add";
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
          q ? "Nothing installed matches. Try the GDTF Share tab." : "The library is empty. Use the GDTF Share tab to download fixtures, or drop .gdtf files into fixtures_inbox/ and import them from Settings."));
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
    t = setTimeout(() => (source === "lib" ? searchLib() : searchShare()), source === "lib" ? 120 : 350);
  });
  tabs.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    source = b.dataset.src;
    [...tabs.children].forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    chosen = null;
    addBtn.disabled = true;
    shareNote.replaceChildren();
    if (source === "lib") searchLib(); else searchShare();
  });

  addBtn.addEventListener("click", async () => {
    if (!chosen) return;
    addBtn.disabled = true;
    try {
      let item = chosen;
      if (source === "share") {
        const d = await post("/api/gdtf/download", { rid: chosen.rid });
        if (d.error) throw new Error(d.error);
        toast(d.summary || "Downloaded", "ok");
        const found = await get("/api/fixtures?q=" + encodeURIComponent(`${d.manufacturer} ${d.model}`));
        item = (found.results || [])[0];
        if (!item) throw new Error("downloaded, but it did not appear in the library");
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
  searchLib();
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
export async function openSettings() {
  const status = await get("/api/status").catch(() => ({}));
  const con = status.console || {};
  const v = (state.snap && state.snap.venue) || {};
  const w = h("input", { type: "number", min: 2, step: 0.5, value: v.width_m || "" , placeholder: "auto" });
  const d = h("input", { type: "number", min: 2, step: 0.5, value: v.depth_m || "", placeholder: "auto" });
  const ht = h("input", { type: "number", min: 2, step: 0.5, value: v.height_m || "", placeholder: "auto" });
  const midi = status.midi || {};
  const body = h("div",
    h("h3", "Stage"),
    h("div.form-grid",
      h("label.field", h("span", "Width m"), w), h("label.field", h("span", "Depth m"), d), h("label.field", h("span", "Height m"), ht),
      h("div.field", h("span", " "), h("button.btn", {
        onclick: () => run("set_venue", { width_m: +w.value || 0, depth_m: +d.value || 0, height_m: +ht.value || 0, name: v.name || "stage" }, { toast: true }),
      }, "Apply"))),
    h("h3", "Output"),
    h("dl.kv", { style: { display: "grid", gridTemplateColumns: "140px 1fr", gap: "6px 12px", margin: 0 } },
      h("dt.muted", "Protocol"), h("dd", { style: { margin: 0 } }, (con.transport || "artnet").toUpperCase()),
      h("dt.muted", "Sending to"), h("dd", { style: { margin: 0 } }, `${con.host}:${con.port}` + (con.multicast ? " (each universe's multicast group)" : con.broadcast ? " (broadcast on your lighting network)" : "")),
      h("dt.muted", "This computer"), h("dd", { style: { margin: 0 } }, con.local_ip || "unknown"),
      h("dt.muted", "Frame rate"), h("dd", { style: { margin: 0 } }, `${con.hz} Hz`)),
    h("p.muted.small", "Change these in the .env file (DMX_TRANSPORT, DMX_HOST, DMX_HZ) and restart."),
    h("div.row-btns", h("button.btn", { onclick: () => import("./fixtures.js").then((m) => m.scanRig()) }, "Scan for Art-Net nodes")),
    h("h3", "Fixture library"),
    h("div.row-btns",
      h("span.muted.small", `${status.fixtures ?? "?"} fixture types installed.`),
      h("button.btn", {
        onclick: async () => {
          const r = await post("/api/fixtures/import", {}).catch((e) => ({ error: e.message }));
          if (r.error) toast(r.error, "bad");
          else toast(`Imported ${(r.imported || []).length} file(s) from fixtures_inbox/` + ((r.errors || []).length ? ` · ${(r.errors || []).length} failed` : ""), (r.errors || []).length ? "bad" : "ok");
        },
      }, "Import .gdtf files from fixtures_inbox/")),
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
