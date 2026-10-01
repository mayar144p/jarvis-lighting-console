// Dialogs: add fixtures, fixture profiles, DMX channels, CSV import, cues,
// shows, settings and help.
import { FixturePreview } from "/js/stage/stage.js";
import { get, post } from "./api.js";
import { webMidiOn, setWebMidi, webMidiSupported, webMidiInputs, webMidiError } from "./webmidi.js";
import { openNodeMonitor, openMidiMonitor, virtualNodeOn } from "./monitors.js";
import { state, on, patch, selected, outputState } from "./store.js";
import { run } from "./actions.js";
import { $, h, modal, toast, confirmBox, promptBox, menu } from "./ui.js";

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
    foot: [h("button.btn", { title: "For a light or effect no library has", onclick: () => openManualFixture(() => { chosen = null; searchAll(); }) }, "From its manual…"),
      h("span.muted.small.grow", "New fixtures are addressed after the last one and hung where that kind of light goes. Drag them on the stage to move them."),
      h("button.btn", { onclick: () => close() }, "Close"), addBtn],
    onClose: () => { if (preview) { preview.destroy(); preview = null; } },
  });

  preview = new FixturePreview(pv);

  // the fixture shown on the right must be one of the results on the left:
  // a pick left over from before the search was what "Add to stage" added
  const sameItem = (a, b) => !!(a && b) && (a.id ?? a.key ?? a.rid) === (b.id ?? b.key ?? b.rid)
    && a.manufacturer === b.manufacturer && (a.model || a.fixture) === (b.model || b.fixture);
  function noPick() {
    chosen = null;
    addBtn.disabled = true;
    right.classList.add("no-pick");
    title.textContent = "Nothing picked";
    meta.textContent = "Search above, then pick a fixture from the list.";
  }
  function keepOrFirst(items) {
    showPick((chosen && items.find((x) => sameItem(x, chosen))) || items[0]);
  }

  function showPick(item) {
    chosen = item;
    right.classList.remove("no-pick");
    [...list.children].forEach((b) => b.classList.toggle("on", b._item === item));
    const b = item.body || {};
    pv.querySelector(".cap").replaceChildren(h("b", b.label || "Fixture"), " · ", b.brand_name || item.manufacturer || "");
    if (preview && item.body) preview.show(item.body, `${item.manufacturer || ""} ${item.model || item.fixture || ""}`);
    title.textContent = `${item.manufacturer || ""} ${item.model || item.fixture || ""}`.trim();
    const modes = item.modes || [];
    mode.replaceChildren(...modes.map((m) => h("option", { value: m.name },
      `${m.name} (${m.channel_count ?? m.dmxfootprint ?? "?"} ch)${m.name === item.default_mode ? " - recommended" : ""}`)));
    if (item.default_mode) mode.value = item.default_mode;
    const origin = item._origin || source;
    if (origin === "share") {
      meta.textContent = `GDTF Share · revision ${item.revision || "?"} · downloads the manufacturer's file, then adds it`;
      addBtn.textContent = "Download and add";
    } else if (origin === "open") {
      meta.textContent = `${item.library} · ${modes.length} DMX mode(s) · community-made: check the mode against the light's manual`;
      addBtn.textContent = "Install and add";
    } else {
      meta.replaceChildren(`${modes.length} DMX mode(s)` + (item.source ? ` · from ${item.source}` : "") + " · ",
        h("button.linkish", {
          title: "Remove this fixture from your installed library (e.g. a bad read of a manual)",
          onclick: async () => {
            if (!item.id || !await confirmBox("Delete from library",
              `Remove "${item.manufacturer} ${item.model}" from your installed fixtures? Lights already on stage keep working until you remove them.`,
              { ok: "Delete", danger: true })) return;
            const r = await post("/api/fixtures/delete", { id: item.id }).catch((e) => ({ error: e.message }));
            if (r.error) { toast(r.error, "bad"); return; }
            toast(`Deleted ${r.manufacturer} ${r.model}` + ((r.patched || []).length
              ? ` - remove #${r.patched.join(", #")} from the stage and add the right one` : ""), "ok");
            chosen = null;
            addBtn.disabled = true;
            if (source === "all") searchAll(); else searchLib();
          },
        }, "Delete from library"));
      addBtn.textContent = "Add to stage";
    }
    addBtn.disabled = false;
  }

  // Every keystroke starts a search and they finish in any order: only the
  // newest may write the list, or a slow reply for "cobr" lands after the
  // one for "cobra 120" and the right results vanish.
  let seq = 0;

  async function searchLib() {
    const my = ++seq;
    const q = search.value.trim();
    try {
      const d = await get("/api/fixtures?q=" + encodeURIComponent(q));
      if (my !== seq) return;
      const rows = d.results || [];
      list.replaceChildren(...rows.map((r) => {
        const b = h("button.lib-item", { onclick: () => showPick(r) },
          h("div.li-t", h("b", `${r.manufacturer} ${r.model}`),
            h("small", [(r.body && r.body.label) || "", `${(r.modes || []).length} mode(s)`].filter(Boolean).join(" · "))));
        b._item = r;
        return b;
      }));
      if (!rows.length) {
        noPick();
        list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } },
          q ? "Nothing installed matches. Try the Libraries or GDTF Share tab." : "The library is empty. Search the Libraries tab (thousands of lights, offline) or GDTF Share, or drop .gdtf / .qxf files into fixtures_inbox/ and import them from Settings."));
      } else keepOrFirst(rows);
    } catch (err) {
      list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, err.message));
    }
  }

  async function searchShare() {
    const my = ++seq;
    const st = await get("/api/gdtf/status").catch(() => ({}));
    if (my !== seq) return;
    if (!st.signed_in && !st.configured) {
      // Sign-in is offered whenever there is no live session - even with
      // a saved catalogue to browse, a download needs one.
      shareNote.replaceChildren(
        h("p", { style: { margin: "0 0 8px" } }, (st.catalogue ? `${st.catalogue} fixtures saved to browse. ` : "")
          + (st.last_error_code === "unauthorized" ? "Your GDTF Share session expired: sign in again to download. " : "Sign in to download. ")
          + "Free gdtf-share.com account; the password stays in this app's memory only."),
        shareLogin());
      if (!st.catalogue) { list.replaceChildren(); return; }
    } else {
      shareNote.textContent = st.catalogue ? `${st.catalogue} fixtures in the catalogue` : "";
    }
    const q = search.value.trim();
    if (q.length < 2) { list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, "Type at least two letters to search the Share.")); return; }
    list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, "Searching…"));
    const d = await get("/api/gdtf/search?limit=80&q=" + encodeURIComponent(q)).catch((e) => ({ error: e.message }));
    if (my !== seq) return;
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
    const my = ++seq;
    const q = search.value.trim();
    if (q.length < 2) {
      list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, "Type a brand or model: the Open Fixture Library and QLC+ are searched offline."));
      return;
    }
    const d = await get("/api/fixtures/library?limit=80&q=" + encodeURIComponent(q)).catch((e) => ({ error: e.message }));
    if (my !== seq) return;
    if (d.error) { list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, d.error)); return; }
    const libs = d.libraries || [];
    shareNote.textContent = libs.map((l) => `${l.name}: ${l.fixtures} fixtures (${l.licence})`).join(" · ");
    const rows = d.results || [];
    if (!rows.length) {
      noPick();
      list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, "Nothing matches. Try fewer words, or the GDTF Share tab."));
      return;
    }
    list.replaceChildren(...rows.map((r) => {
      const item = { ...r, modes: (r.modes || []).map(([n, c]) => ({ name: n, channel_count: c })) };
      const b = h("button.lib-item", { onclick: () => showPick(item) },
        h("div.li-t", h("b", `${r.manufacturer} ${r.model}`),
          h("small", [r.close ? "close match" : "", r.src === "ofl" ? "OFL" : "QLC+", r.type || "", `${(r.modes || []).length} mode(s)`].filter(Boolean).join(" · "))));
      b._item = item;
      return b;
    }));
    if (list.firstChild && list.firstChild._item) keepOrFirst([...list.children].map((x) => x._item).filter(Boolean));
  }

  // "13 ch" / "6 or 12 ch": the channel counts tell two versions apart
  const chCounts = (modes) => {
    const n = [...new Set((modes || []).map((m) => m.channel_count ?? m.dmxfootprint).filter((x) => x))];
    return n.length ? `${n.join(" or ")} ch` : `${(modes || []).length} mode(s)`;
  };

  // One box, every source: installed first, then the Jarvis library,
  // OFL and QLC+ (offline), then GDTF Share when signed in.
  async function searchAll() {
    const my = ++seq;
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
    if (my !== seq) return;
    const rows = [];
    for (const r of inst.results || []) {
      rows.push(row({ ...r, _origin: "lib" }, `${r.manufacturer} ${r.model}`,
        [(r.body && r.body.label) || "", chCounts(r.modes)], "installed"));
    }
    for (const r of lib.results || []) {
      const item = { ...r, _origin: "open", modes: (r.modes || []).map(([n, c]) => ({ name: n, channel_count: c })) };
      rows.push(row(item, `${r.manufacturer} ${r.model}`, [r.close ? "close match" : "", r.type || "", chCounts(item.modes)],
        r.src === "ofl" ? "OFL" : r.src === "qlc" ? "QLC+" : "Jarvis"));
    }
    list.replaceChildren(...rows);
    if (!rows.length) {
      noPick();
      list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } },
        q.length < 2 ? "Type a brand or model (e.g. \u201cfunfetti\u201d, \u201cwave 360\u201d)." : "Nothing matches in the installed fixtures or the libraries."));
    } else keepOrFirst(rows.map((x) => x._item));
    if (q.length < 2) return;
    const st = await get("/api/gdtf/status").catch(() => ({}));
    if (!(st.signed_in || st.catalogue) || my !== seq) return;
    const d = await get("/api/gdtf/search?limit=30&q=" + encodeURIComponent(q)).catch(() => ({}));
    if (my !== seq || source !== "all") return;
    // nothing found offline: say so above the Share results, not "nothing matches"
    if (!rows.length && (d.results || []).length) list.replaceChildren();
    for (const r of d.results || []) {
      list.append(row({ ...r, model: r.fixture, _origin: "share" }, `${r.manufacturer} ${r.fixture}`,
        [(r.body && r.body.label) || "", `rev ${r.revision || "?"}`], "GDTF Share"));
    }
  }

  // A sign-in box on top of the dialog; resolves true once signed in.
  function shareSignIn() {
    return new Promise((resolve) => {
      const user = h("input", { type: "text", placeholder: "gdtf-share.com user", autocomplete: "username" });
      const pass = h("input", { type: "password", placeholder: "password", autocomplete: "current-password" });
      const msg = h("div.muted.small", "Your GDTF Share session has ended. Sign in to download this fixture. The password stays in this app's memory only.");
      let done = false;
      const finish = (ok) => { if (!done) { done = true; resolve(ok); } };
      const go = async () => {
        const d = await post("/api/gdtf/login", { user: user.value, password: pass.value }).catch((e) => ({ error: e.message }));
        if (d.error) { msg.textContent = d.error; return; }
        toast("Signed in to GDTF Share", "ok");
        finish(true);
        closeBox();
      };
      pass.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      const closeBox = modal({
        title: "Sign in to GDTF Share",
        body: h("div.form-grid", h("label.field", h("span", "User"), user), h("label.field", h("span", "Password"), pass), msg),
        foot: [h("button.btn", { onclick: () => closeBox() }, "Cancel"), h("button.btn.primary", { onclick: go }, "Sign in")],
        onClose: () => finish(false),
      });
      setTimeout(() => user.focus(), 50);
    });
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
  search.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    clearTimeout(t);
    if (source === "all") searchAll(); else if (source === "lib") searchLib(); else if (source === "open") searchOpen(); else searchShare();
  });
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
        let d = await post("/api/gdtf/download", { rid: chosen.rid });
        if (d.error && ["no_session", "unauthorized", "no_credentials"].includes(d.code)) {
          if (!(await shareSignIn())) throw new Error("not signed in to GDTF Share");
          d = await post("/api/gdtf/download", { rid: chosen.rid });
        }
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
  // where it goes: a new cue at the end, or over / before an existing one
  const where = h("select.select");
  const modeBox = h("div.chip-row");
  let mode = "replace";
  const drawWhere = () => {
    const pb = pbs.find((p) => p.n === +pbSel.value) || { stack: [] };
    const stack = pb.stack || [];
    where.replaceChildren(h("option", { value: "" }, `New cue ${stack.length + 1} at the end`),
      ...stack.map((c) => h("option", { value: c.n }, `Cue ${c.n}: ${c.name || ""}`)));
    drawMode();
  };
  const drawMode = () => {
    const over = where.value !== "";
    modeBox.hidden = !over;
    modeBox.replaceChildren(...[["replace", "Replace", "The cue becomes exactly what the programmer holds"],
      ["merge", "Merge", "Add the programmer's changes into the cue, keep the rest of it"],
      ["insert", "Insert before", "A new cue at this number; the later cues move down"]].map(([v, label, title]) =>
      h("button.chip" + (mode === v ? ".on" : ""), { type: "button", title, onclick: () => { mode = v; drawMode(); } }, label)));
    fade.placeholder = over ? "keep" : "";
  };
  pbSel.addEventListener("change", drawWhere);
  where.addEventListener("change", drawMode);
  drawWhere();
  const close = modal({
    title: "Record cue",
    body: h("div.form-grid",
      h("label.field", h("span", "Playback"), pbSel), h("label.field", h("span", "Where"), where),
      h("div.field", h("span", " "), modeBox),
      h("label.field", h("span", "Name"), name),
      h("label.field", h("span", "Fade (s)"), fade), h("label.field", h("span", "Hold before auto-follow (s)"), hold)),
    foot: [h("span.muted.small.grow", "Records what the programmer holds, then clears it. Over a cue, its name and times are kept unless you type new ones."),
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", {
        onclick: async () => {
          const params = { playback: +pbSel.value, name: name.value.trim() };
          if (where.value !== "") { params.cue = +where.value; params.mode = mode; }
          if (fade.value !== "") params.fade = +fade.value || 0;
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

// A cue's own fade per part ("colour snaps, movers glide 4 s"): the chip
// shows how many parts are timed, the menu sets one.
const PARTS = [["intensity", "Level"], ["colour", "Colour"], ["position", "Position"], ["beam", "Beam"]];
function partTimes(n, c, refresh) {
  const t = c.times || {};
  const set = Object.keys(t).length;
  return h("button.chip.cue-parts" + (set ? ".on" : ""), {
    title: set ? PARTS.filter(([k]) => k in t).map(([k, l]) => `${l} ${t[k]} s`).join(" · ")
      : "Give level, colour, position or beam a fade of its own",
    onclick: (e) => menu(e.currentTarget, PARTS.map(([k, l]) => ({
      label: `${l}: ${k in t ? t[k] + " s" : `same as the cue (${c.fade_s ?? 0} s)`}`,
      run: async () => {
        const v = await promptBox(`${l} fade`, "Seconds (empty = same as the cue)", k in t ? String(t[k]) : "", { ok: "Set" });
        if (v === null) return;
        run("edit_cue", { playback: n, cue: c.n, times: { [k]: v.trim() === "" ? null : +v } }).then(refresh);
      },
    }))),
  }, set ? `${set} part${set > 1 ? "s" : ""}` : "parts");
}

const CUE_ACTS = [
  ["quick_press", "Press a button", "id", "button id or name"],
  ["macro_run", "Run a macro", "id", "macro name"],
  ["cue_go", "GO a cue list", "playback", "playback number"],
  ["playback_release", "Release a cue list", "playback", "playback number"],
  ["timeline_play", "Play the timeline", "", ""],
  ["timeline_pause", "Pause the timeline", "", ""],
  ["timeline_seek", "Timeline to (s)", "t", "seconds"],
  ["tempo_set", "Set the tempo", "bpm", "BPM"],
  ["step_fx_run", "Run a step effect", "id", "step effect id"],
  ["master", "Set the master", "level", "0-100"],
];

function openCueActions(n, c, done) {
  const list = JSON.parse(JSON.stringify(c.actions || []));
  const box = h("div");
  const draw = () => box.replaceChildren(...(list.length ? list.map((a, i) => {
    const def = CUE_ACTS.find((d) => d[0] === a.action) || [a.action, a.action, "", ""];
    const inp = def[2] ? h("input", { type: "text", value: a.args[def[2]] ?? "", placeholder: def[3] }) : null;
    if (inp) inp.addEventListener("change", () => { const v = inp.value.trim(); a.args[def[2]] = v !== "" && !isNaN(+v) ? +v : v; });
    return h("div.row-btns", h("b", def[1]), inp, h("button.btn.small.ghost", { onclick: () => { list.splice(i, 1); draw(); } }, "×"));
  }) : [h("p.muted.small", "Nothing yet: add what this cue does as it plays.")]));
  const add = h("select.select", h("option", { value: "" }, "+ add…"), ...CUE_ACTS.map((d) => h("option", { value: d[0] }, d[1])));
  add.addEventListener("change", () => { if (add.value) list.push({ action: add.value, args: {} }); add.value = ""; draw(); });
  draw();
  const close = modal({
    title: `Cue ${c.n} · actions`, body: h("div", box, add),
    foot: [h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", { onclick: async () => {
        const r = await run("cue_set", { playback: n, cue: c.n, actions: list }, { toast: true });
        if (r.ok) { close(); done && done(); }
      } }, "Save")],
  });
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
      const tr = h("tr" + (i === pb.index ? ".sel" : ""), { draggable: "true", title: "Drag to reorder" },
        h("td.mono.cue-grip", "⋮⋮ ", c.n, c.block ? h("span.chip", { title: "Block: starts afresh" }, "B") : null,
          (c.actions || []).length ? h("span.chip", { title: c.actions.map((a) => a.action).join(", ") }, "▶" + c.actions.length) : null), h("td", nm, cueTimeline(c, longest),
          (c.fx || []).length ? h("div.cue-fx", { title: "Effects this cue runs (they stop at the next cue or on release)" },
            "⚡ " + c.fx.join(", ")) : null),
        h("td", fd, partTimes(n, c, () => refresh())), h("td", hd),
        h("td", followCell(n, c, () => refresh())),
        h("td", h("div.row-btns",
          h("button.btn.small", { title: "Go to this cue", onclick: () => run("cue_go", { playback: n, cue: c.n }) }, "Go"),
          h("button.btn.small.ghost", { title: "Move up", disabled: i === 0, onclick: () => run("move_cue", { playback: n, cue: c.n, to: c.n - 1 }).then(refresh) }, "↑"),
          h("button.btn.small.ghost", { title: "Move down", disabled: i === stack.length - 1, onclick: () => run("move_cue", { playback: n, cue: c.n, to: c.n + 1 }).then(refresh) }, "↓"),
          h("button.btn.small.ghost", { title: "Update this cue from the programmer: merge or replace", onclick: (e) => menu(e.currentTarget, [
            { label: "Merge the programmer into it", hint: "adds the changes, keeps the rest", run: () => run("record_cue", { playback: n, cue: c.n, mode: "merge" }, { toast: true }) },
            { label: "Replace it with the programmer", hint: "exactly what the programmer holds", run: () => run("record_cue", { playback: n, cue: c.n, mode: "replace" }, { toast: true }) },
            { label: "Record a new cue before it", run: () => run("record_cue", { playback: n, cue: c.n, mode: "insert" }, { toast: true }) },
            { label: "Merge, cue only", hint: "tracking: the change stops at this cue", disabled: !pb.tracking, run: () => run("record_cue", { playback: n, cue: c.n, mode: "merge", cue_only: true }, { toast: true }) },
            "-",
            { label: "Preview edit…", hint: "edit it in 3D only - the rig doesn't see", run: () => run("blind", { playback: n, cue: c.n }, { toast: true }) },
            { label: (c.block ? "✓ " : "") + "Block", hint: "tracking: start afresh here", disabled: !pb.tracking, run: () => run("cue_set", { playback: n, cue: c.n, block: !c.block }, { toast: true }).then(refresh) },
            { label: `Actions… ${(c.actions || []).length ? "(" + c.actions.length + ")" : ""}`, hint: "buttons, macros, other lists, timeline, tempo", run: () => openCueActions(n, c, refresh) },
          ]) }, "Update ▾"),
          h("button.btn.small.ghost", { title: "Insert an empty cue below", onclick: () => run("insert_cue", { playback: n, at: c.n + 1 }).then(refresh) }, "+"),
          h("button.btn.small.ghost", { title: "Delete", onclick: () => run("delete_cue", { playback: n, cue: c.n }).then(refresh) }, "×"))));
      // drag a row onto another to move the cue there
      tr.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", String(c.n)); tr.classList.add("dragging"); });
      tr.addEventListener("dragend", () => tr.classList.remove("dragging"));
      tr.addEventListener("dragover", (e) => { e.preventDefault(); tr.classList.add("drop"); });
      tr.addEventListener("dragleave", () => tr.classList.remove("drop"));
      tr.addEventListener("drop", (e) => {
        e.preventDefault();
        tr.classList.remove("drop");
        const from = +e.dataTransfer.getData("text/plain");
        if (from && from !== c.n) run("move_cue", { playback: n, cue: from, to: c.n }, { toast: true }).then(refresh);
      });
      return tr;
    });
    const delay = (pb.follow || {}).delay;
    return h("div",
      h("div.row-btns",
        h("button.btn.small" + (pb.tracking ? ".on" : ".ghost"), { title: "Tracking: a cue holds only what it changes, the rest carries on from the cues before. Off: each cue is the whole look.", onclick: () => run("playback_mode", { playback: n, tracking: !pb.tracking }, { toast: true }).then(refresh) }, pb.tracking ? "Tracking" : "Cue only"),
        h("button.btn.small" + (pb.mib ? ".on" : ".ghost"), { title: "Move in black: lights that are dark now and on in the next cue move there while dark", onclick: () => run("playback_mode", { playback: n, mib: !pb.mib }, { toast: true }).then(refresh) }, "Move in black")),
      h("table.chan-table", h("thead", h("tr", h("th", "#"), h("th", "Name"), h("th", "Fade s"), h("th", "Hold s"),
        h("th", { title: "Stack inherits the playback's auto-follow; Wait holds for GO; Auto runs the next cue by itself" }, "Then"), h("th", ""))),
        h("tbody", ...(rows.length ? rows : [h("tr", h("td", { colspan: 6, class: "muted" }, "No cues yet - set a look and record one."))]))),
      h("p.muted.small", "Then: ", h("b", "Stack"), ` follows the playback's auto-follow${delay ? ` (${delay}s)` : ""}, `,
        h("b", "Wait"), " holds for GO, ", h("b", "Auto"), " runs the next cue after the seconds given."));
  };
  const body = h("div", render());
  // live: redraw when the cue list changes (not while typing in it)
  const sig = () => JSON.stringify(((state.snap && state.snap.playbacks) || []).find((p) => p.n === n) || {});
  let last = sig();
  const refresh = () => { last = sig(); body.replaceChildren(render()); };
  const off = on("snapshot", () => {
    if (!document.body.contains(body)) { off(); return; }
    if (body.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;
    if (sig() !== last) refresh();
  });
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
    { label: "Paperwork…", hint: "light plot, patch sheet, rigging - print / PDF", run: () => window.open("/plot.html", "_blank") },
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
    "-",
    { label: "Ready? check…", hint: "before doors", run: openReadyCheck },
    current ? { label: "Earlier versions…", run: () => openVersions(current) } : null,
    current ? { label: "Export (download)", run: () => exportShow(current) } : null,
  ]);
}

// Before doors: what would bite during the show, each with what to press.
export async function openReadyCheck() {
  const r = await run("ready_check", {});
  if (!r.ok) return;
  const ICON = { ok: "✓", info: "i", warn: "!", bad: "✕" };
  const body = h("div.ready",
    h("p.ready-head." + r.worst, r.ready ? "Ready for doors." : r.worst === "bad" ? "Fix these before the show." : "Worth a look before the show."),
    ...r.items.map((i) => h("div.ready-row." + i.level, h("span.ready-ic", ICON[i.level] || "·"),
      h("div", h("div", i.text), i.fix ? h("small.muted", "→ " + i.fix) : null))));
  modal({ title: "Ready?", body });
}

async function openVersions(name) {
  const r = await run("show_versions", { name });
  if (!r.ok) return;
  let close = null;
  const when = (v) => (v.saved ? new Date(v.saved).toLocaleString() : v.id);
  const body = h("div",
    h("p.muted.small", "Every save that changed the show keeps the one before (the last 20). Opening one keeps the current show as a version too."),
    r.versions.length ? h("div.ver-list", ...r.versions.map((v) => h("div.ver-row",
      h("span", when(v)), h("small.muted", `${Math.max(1, Math.round(v.bytes / 1024))} KB`),
      h("button.btn.small", { onclick: async () => {
        if (!(await confirmBox("Open this version", `Open the version saved ${when(v)}? The show as it is now is kept as a version.`, { ok: "Open" }))) return;
        const res = await run("restore_version", { name, id: v.id }, { toast: true });
        if (res.ok && close) close();
      } }, "Open"))))
      : h("p.muted", "No earlier versions yet: they appear after the next save that changes something."));
  close = modal({ title: `Versions of “${name}”`, body });
}

async function exportShow(name) {
  const r = await run("show_export", { name });
  if (!r.ok) return;
  const url = URL.createObjectURL(new Blob([r.text], { type: "application/json" }));
  const a = h("a", { href: url, download: r.filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
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
    h("p.muted.small", "Saved with the show, so each venue keeps its own node. Changes apply straight away, no restart."),
    h("div.row-btns",
      h("button.btn", { onclick: () => openNodeMonitor() }, virtualNodeOn() ? "Virtual node (on)…" : "Virtual node…"),
      h("span.muted.small", "test the whole output and RDM with no hardware")));
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
  const gigBox = h("input", { type: "checkbox" });
  gigBox.checked = document.body.classList.contains("gig");
  gigBox.addEventListener("change", () => {
    document.body.classList.toggle("gig", gigBox.checked);
    try { localStorage.setItem("jarvis.gig", gigBox.checked ? "1" : "0"); } catch (e) { /* ignore */ }
  });
  const midi = status.midi || {};
  const body = h("div",
    h("h3", "Venue"),
    h("p.muted.small", auto ? "No room drawn yet: the 3D view sizes one around your lights."
      : `${v.name || "Room"}: ${room.width} × ${room.depth} m, ${room.height} m ceiling · ${(v.rigging || []).length} rigging · ${(v.zones || []).length} zones`),
    h("div.row-btns", h("button.btn.primary", { onclick: () => import("./roomdialog.js").then((m) => m.openRoomDialog()) }, "Make the room…"),
      h("span.muted.small", "a shape and its sizes, described in words, a template, a floor plan, or drawn")),
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
    h("h3", "Screen"),
    h("label.check", gigBox, h("span", "Gig mode: big buttons and text everywhere")),
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
    h("p.muted.small", "On the desk computer: " + (midi.enabled ? (midi.open ? `listening to ${midi.device}` : (midi.error || "no MIDI device found")) : "MIDI is off (MIDI_ENABLED=false).")),
    webMidiRow(),
    h("div.row-btns", h("button.btn", { onclick: () => openMidiMonitor() }, "MIDI monitor…"),
      h("span.muted.small", "see what a controller sends and what it did")),
    oscRow(),
    h("h3", "AI"),
    h("p.muted.small", status.llm_configured ? `Using ${status.model}` : "No AI key: the copilot uses its offline compiler. Add LLM_API_KEY to .env for the full copilot."));
  modal({ title: "Settings", body, wide: false });
}

// OSC in: TouchOSC, Bitfocus Companion, QLab ... play the show.
function oscRow() {
  const o = (state.snap && state.snap.osc) || {};
  const box = h("input", { type: "checkbox" });
  box.checked = !!o.on;
  const port = h("input", { type: "number", min: 1024, max: 65535, value: o.port || 8000, style: { width: "90px" } });
  const note = h("span.muted.small", o.on ? `listening on UDP ${o.port} · ${o.count} message(s)` + (o.last ? ` · last ${o.last.address}` : "") : (o.error || ""));
  const apply = async () => {
    const r = await run("osc", { state: box.checked, port: +port.value || 8000 }, { toast: true });
    if (!r.ok) box.checked = false;
  };
  box.addEventListener("change", apply);
  port.addEventListener("change", () => { if (box.checked) apply(); });
  return h("div",
    h("label.check", box, h("span", "OSC in (TouchOSC, Companion, QLab) on UDP port "), port),
    note,
    h("p.muted.small", "/jarvis/go 1 · /jarvis/cue 1 3 · /jarvis/master 0.8 · /jarvis/blackout 1 · /jarvis/button/q1-3 1 · /jarvis/macro Walk-in · /jarvis/cmd \"1-4 red\" · /jarvis/tap · /jarvis/bpm 128. Anyone on this network can play the show while it is on."));
}

// MIDI on this device: a controller plugged into the computer the
// browser runs on plays the buttons given its notes.
function webMidiRow() {
  const box = h("input", { type: "checkbox" });
  box.checked = webMidiOn();
  const note = h("span.muted.small", "");
  const show = () => {
    const ins = webMidiInputs();
    note.textContent = !webMidiSupported() ? "This browser has no MIDI (use Chrome or Edge)."
      : !box.checked ? "" : webMidiError() || (ins.length ? `Listening to ${ins.join(", ")}` : "No controller plugged in yet.");
  };
  box.disabled = !webMidiSupported();
  box.addEventListener("change", async () => {
    const ok = await setWebMidi(box.checked);
    if (!ok) box.checked = false;
    show();
  });
  show();
  return h("div", h("label.check", box, h("span", "MIDI in the browser: a controller plugged into the computer this screen runs on plays the buttons")), note);
}

// ================================================================== help
const KEYS = [
  ["Space / Enter", "GO on the focused playback"], ["B", "back one cue"], ["X", "blackout"],
  ["/ or Ctrl+K", "command bar"], ["Ctrl+Z / Ctrl+Shift+Z", "undo / redo"], ["Ctrl+S", "save the show"],
  ["A / Shift+A", "select all / none"], ["1 … 9", "select fixture 1–9 (Shift adds)"], ["G", "group the selection"],
  ["L", "locate"], ["C", "clear the programmer"], ["R", "record a cue"], ["O", "overwrite the current cue"], ["I", "insert a cue after the current one"], ["D", "delete the current cue"], ["↑ ↓", "intensity ±5 (Shift ±1)"],
  ["T / Shift+T", "tap the tempo / this is beat 1"], ["H / Shift+H", "highlight / solo the selection"], ["F", "frame the selection on stage"], ["Esc", "close / leave full screen"], ["?", "this help"],
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

// ================================================= fixture from a manual
// Any light or effect no library has: paste the DMX chart or drop the
// manual's PDF, check the table, save.  Nothing is stored until Save.
const FIXTURE_FUNCTIONS = ["dimmer", "red", "green", "blue", "white", "amber", "uv", "cyan", "magenta", "yellow",
  "pan", "pan fine", "tilt", "tilt fine", "pan/tilt speed", "shutter", "strobe", "colour wheel", "colour macro",
  "gobo wheel", "gobo rotation", "prism", "zoom", "focus", "frost", "iris",
  "fx fire", "fx arm", "fx fan", "fog output", "fx height", "fx mode",
  "laser output", "laser pattern", "laser size", "laser rotation", "laser x", "laser y", "laser speed", "laser colour", "laser beam",
  "setting", "unused"];
const FIXTURE_TYPES = [["light", "Light"], ["laser", "Laser"], ["confetti", "Confetti"], ["co2", "CO2 jet"],
  ["flame", "Flame"], ["spark", "Spark fountain"], ["fog", "Fog"], ["haze", "Haze"], ["bubble", "Bubbles"],
  ["snow", "Snow"], ["other", "Other effect"]];

async function pdfText(file) {
  const pdfjs = await import("/vendor/pdfjs/pdf.min.mjs");
  pdfjs.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.min.mjs";
  const doc = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise;
  const pages = [];
  for (let i = 1; i <= Math.min(doc.numPages, 60); i++) {
    const page = await doc.getPage(i);
    const tc = await page.getTextContent();
    // rebuild lines from the text runs' y positions
    const rows = new Map();
    for (const it of tc.items) {
      const y = Math.round(it.transform[5]);
      rows.set(y, (rows.get(y) || "") + (rows.has(y) ? " " : "") + it.str);
    }
    pages.push([...rows.entries()].sort((a, b) => b[0] - a[0]).map(([, t]) => t).join("\n"));
  }
  return pages.join("\n");
}

export function openManualFixture(onSaved) {
  const maker = h("input", { type: "text", placeholder: "e.g. Chauvet DJ" });
  const model = h("input", { type: "text", placeholder: "e.g. Funfetti Shot" });
  const text = h("textarea", { rows: 8, placeholder: "Paste the DMX chart here (from the manual or its PDF), e.g.\n1 Off/On\n000-009 Off\n010-255 On" });
  const file = h("input", { type: "file", accept: ".pdf,.txt,application/pdf,text/plain" });
  const status = h("div.muted.small");
  const review = h("div.man-review");
  let draft = null;
  file.addEventListener("change", async () => {
    const f = file.files && file.files[0];
    if (!f) return;
    status.textContent = "Reading the file…";
    try {
      text.value = f.name.toLowerCase().endsWith(".pdf") ? await pdfText(f) : await f.text();
      status.textContent = `Read ${f.name}. Press "Read the chart".`;
    } catch (err) {
      status.textContent = "Could not read that file: " + err.message;
    }
  });
  const readBtn = h("button.btn.primary", {
    onclick: async () => {
      status.textContent = "Reading the DMX chart…";
      const d = await post("/api/fixtures/from_manual", { text: text.value, manufacturer: maker.value, model: model.value }).catch((e) => ({ error: e.message }));
      if (d.error) { status.textContent = d.error; return; }
      draft = d.draft;
      status.textContent = (draft.via === "ai" ? "Read by the AI." : "Read by the offline reader.")
        + " Check every channel against the manual before saving." + ((draft.warnings || []).length ? " " + draft.warnings.join(" ") : "");
      renderReview();
    },
  }, "Read the chart");

  function renderReview() {
    if (!draft || !(draft.modes || []).length) { review.replaceChildren(); return; }
    if (draft.manufacturer && !maker.value) maker.value = draft.manufacturer;
    if (draft.model && !model.value) model.value = draft.model;
    const type = h("select.select", ...FIXTURE_TYPES.map(([k, l]) => h("option", { value: k }, l)));
    type.value = draft.type || "light";
    type.addEventListener("change", () => { draft.type = type.value; });
    const tables = draft.modes.map((m) => {
      const name = h("input", { type: "text", value: m.name, style: { width: "160px" } });
      name.addEventListener("input", () => { m.name = name.value; });
      return h("div.man-mode", h("label.field.inline", h("span", "Mode"), name),
        h("table.chan-table", h("thead", h("tr", h("th", "Ch"), h("th", "Name"), h("th", "Does"), h("th", "Values (one per line: 0-9 Off)"))),
          h("tbody", ...m.channels.map((c, i) => {
            const nm = h("input", { type: "text", value: c.name });
            nm.addEventListener("input", () => { c.name = nm.value; });
            const fn = h("select.select", ...FIXTURE_FUNCTIONS.map((f) => h("option", { value: f }, f)));
            fn.value = c.function;
            fn.addEventListener("change", () => { c.function = fn.value; });
            const rg = h("textarea", { rows: Math.min(4, Math.max(1, c.ranges.length)) });
            rg.value = c.ranges.map(([lo, hi, l]) => `${lo}-${hi} ${l}`).join("\n");
            rg.addEventListener("change", () => {
              c.ranges = rg.value.split("\n").map((ln) => /^\s*(\d{1,3})\s*[-–]\s*(\d{1,3})\s*(.*)$/.exec(ln)).filter(Boolean)
                .map((x) => [+x[1], +x[2], x[3].trim()]);
            });
            return h("tr", h("td.mono", i + 1), h("td", nm), h("td", fn), h("td", rg));
          }))));
    });
    review.replaceChildren(h("div.form-grid", h("label.field", h("span", "Kind of fixture"), type)), ...tables);
  }

  const close = modal({
    title: "Fixture from its manual", wide: true,
    body: h("div.man",
      h("div.form-grid", h("label.field", h("span", "Manufacturer"), maker), h("label.field", h("span", "Model"), model),
        h("label.field", h("span", "Manual (PDF or text)"), file)),
      text, h("div.row-btns", readBtn, status), review),
    foot: [h("span.muted.small.grow", "Jarvis stores exactly this table. Effects fire only from their armed FX buttons."),
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", {
        onclick: async () => {
          if (!draft) { toast("Read the chart first", "bad"); return; }
          if (!maker.value.trim() || !model.value.trim()) { toast("Give the manufacturer and the model, so you can find it later", "bad"); return; }
          draft.manufacturer = maker.value.trim();
          draft.model = model.value.trim();
          const d = await post("/api/fixtures/from_manual/save", { draft }).catch((e) => ({ error: e.message }));
          if (d.error) { toast(d.error, "bad"); return; }
          toast(d.summary, "ok");
          close();
          if (onSaved) onSaved(d.fixture);
        },
      }, "Save to library")],
  });
}

// ====================================================== test this light
// A fixture file can be wrong in ways no code can see: a shutter "open"
// value it never states, a channel order that does not match the light's
// mode.  So a new model gets a 30-second test with the operator watching
// the REAL light: lit? (and if not, find the open value) - moving? - the
// right colours?  A model that passes is not asked about again.
export async function openLightTest(hd) {
  const head = hd.head_no;
  const st = await run("light_test", { head, step: "start" }, { silentError: true });
  if (!st.ok) { toast(st.error || "This light cannot be tested", "bad"); return; }
  const res = { light: null, move: true, colour: true };
  const box = h("div.lt");
  const live = outputState() === "live";
  let closed = false;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const ask = (text, buttons) => new Promise((resolve) => {
    box.replaceChildren(
      live ? null : h("p.out-bad", "The output is not live: the real light will not react. Press Go live first."),
      h("p.lt-q", text),
      h("div.row-btns", ...buttons.map(([label, value, cls]) => h("button.btn" + (cls ? "." + cls : ""), { onclick: () => resolve(value) }, label))));
  });

  // Every DMX channel of the light on its own fader, written straight to
  // the wire: finds what the REAL light needs even when the file (or the
  // mode it describes) is wrong.  "It's on" keeps what was found.
  function faders() {
    return new Promise((resolve) => {
      const rows = (st.slots || []).map((c) => {
        const input = h("input", { type: "range", min: 0, max: 255, value: c.value });
        const out = h("span.mono.small.lt-v", String(c.value));
        let t = 0;
        input.addEventListener("input", () => {
          out.textContent = input.value;
          clearTimeout(t);
          t = setTimeout(() => run("light_test", { head, step: "raw", slot: c.n, value: +input.value }, { silentError: true }), 40);
        });
        const quick = (v) => h("button.chip", { onclick: () => { input.value = v; input.dispatchEvent(new Event("input")); } }, String(v));
        return h("div.lt-ch",
          h("span.lt-n", { title: `DMX address ${c.abs}` }, `${c.n}`),
          h("span.lt-l", { title: `${c.label} (${c.role}) - DMX ${c.abs}` }, c.label),
          input, out, h("span.lt-q2", quick(0), quick(128), quick(255)));
      });
      box.replaceChildren(
        live ? null : h("p.out-bad", "The output is not live: the real light will not react. Press Go live first."),
        h("p.small", "Each fader drives one DMX channel of this light directly (the number is its channel, hover for the address). "
          + "Move them until the real light comes on - usually a dimmer to 255 and a shutter somewhere between 0 and 255. "
          + "If the channel that lights it is not the one called Dimmer or Shutter, the light is in a different DMX mode from the one added."),
        h("div.lt-chs", ...rows),
        h("div.row-btns",
          h("button.btn.primary", { onclick: async () => {
            const r = await run("light_test", { head, step: "keep" }, { silentError: true });
            if (r.ok) toast(r.summary, "ok");
            resolve(true);
          } }, "It's on - keep these"),
          h("button.btn", { onclick: () => resolve(false) }, "Still dark")));
    });
  }

  async function lightStep() {
    const first = await ask(`#${head} ${hd.name || hd.model} should now be ON: full, white, centred. Is the real light on?`,
      [["Yes, it's on", "yes", "primary"], ["No, it's dark", "no"], ["Set channels by hand", "hand"]]);
    if (first === "yes") return true;
    if (first === "hand") return faders();
    const cands = st.gate ? st.candidates || [] : [];
    let stopped = false;
    for (let i = 0; i < cands.length && !closed; i++) {
      await run("light_test", { head, step: "open", value: cands[i] }, { silentError: true });
      const a = await ask(`Trying shutter value ${cands[i]} (${i + 1} of ${cands.length}). Is the real light on now?`,
        [["It's on!", "on", "primary"], ["Still dark", "next"], ["Set channels by hand", "stop"]]);
      if (a === "on") {
        await run("remember_open", { head, value: cands[i] }, { toast: true });
        return true;
      }
      if (a === "stop") { stopped = true; break; }
    }
    if (st.gate) await run("light_test", { head, step: "open", value: st.open }, { silentError: true });
    // still dark: the file may not have named the channel that opens it
    // (a lamp / "control" channel, or a mode whose channels sit elsewhere)
    const hunt = st.hunt || [];
    if (!stopped && hunt.length && !closed && await ask("None of the shutter values lit it. Try each of its other channels in turn? (Some lights need a control channel set before they light.)",
      [["Try them", true, "primary"], ["Set channels by hand", false]])) {
      for (let i = 0; i < hunt.length && !closed; i++) {
        const c = hunt[i];
        await run("light_test", { head, step: "channel", role: c.role, value: c.value }, { silentError: true });
        const a = await ask(`Trying ${c.label} at ${c.value} (${i + 1} of ${hunt.length}). Is the real light on now?`,
          [["It's on!", "on", "primary"], ["Still dark", "next"], ["Set channels by hand", "stop"]]);
        if (a === "on") {
          await run("remember_open", { head, role: c.role, value: c.value }, { toast: true });
          return true;
        }
        if (a === "stop") break;
      }
      await run("light_test", { head, step: "channel" }, { silentError: true });
    }
    if (closed) return false;
    return faders();
  }

  async function sweep(axis) {
    await run("light_test", { head, step: axis, value: 0.3 }, { silentError: true });
    await wait(1200);
    await run("light_test", { head, step: axis, value: 0.7 }, { silentError: true });
    const a = await ask(`It should ${axis === "pan" ? "turn left and right (pan)" : "tip down and up (tilt)"} now. Did the real head do that?`,
      [["Yes", "yes", "primary"], ["Again", "again"], ["No / something else moved", "no"]]);
    if (a === "again") return sweep(axis);
    await run("light_test", { head, step: axis, value: 0.5 }, { silentError: true });
    return a === "yes";
  }

  async function colours() {
    for (const [hex, name] of [["#ff0000", "red"], ["#00ff00", "green"], ["#0000ff", "blue"]]) {
      await run("light_test", { head, step: "colour", hex }, { silentError: true });
      const a = await ask(`It should be ${name.toUpperCase()} now${st.mixing ? "" : " (or the closest colour on its wheel)"}. Is it?`,
        [["Yes", true, "primary"], ["No", false]]);
      if (!a) return false;
    }
    await run("light_test", { head, step: "colour", hex: "#ffffff" }, { silentError: true });
    return true;
  }

  const close = modal({
    title: `Test ${hd.model}`, body: box, wide: true,
    foot: [h("span.muted.small.grow", `Mode ${st.mode} (${st.channels} channels) at ${st.address}. The light gets back what it was doing afterwards.`),
      h("button.btn", { onclick: () => close() }, "Close")],
    onClose: () => { closed = true; run("light_test", { head, step: "end" }, { silentError: true }); },
  });

  res.light = await lightStep();
  if (!closed && res.light && st.pan) res.move = await sweep("pan");
  if (!closed && res.light && st.tilt && res.move) res.move = await sweep("tilt");
  if (!closed && res.light && st.colour) res.colour = await colours();
  if (closed) return;
  const r = await run("light_tested", { head, light: !!res.light, move: res.move, colour: res.colour }, { silentError: true });
  const ok = r.ok && r.tested;
  box.replaceChildren(
    h(ok ? "p.out-ok" : "p.out-bad", ok ? `✓ ${hd.model} passed: it lights, moves and changes colour as Jarvis expects. Every ${hd.model} is ready.`
      : `⚠ ${hd.model} needs attention.`),
    ...((r.advice || []).map((t) => h("p.small", t))),
    ok ? null : h("div.row-btns", h("button.btn", { onclick: () => { close(); openChannels([head]); } }, "Show its DMX channels")));
}
