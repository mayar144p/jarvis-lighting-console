// Dialogs: add fixtures, fixture profiles, DMX channels, CSV import, shows
// and help.  Cues, settings and the one-light dialogs live in their own
// files and are re-exported here, so `./dialogs.js` still has them all.
import { FixturePreview } from "/js/stage/stage.js";
import { get, post, token, modelBytes } from "./api.js";
import { openNewShow, startTour } from "./welcome.js";
import { state, patch, selected } from "./store.js";
import { run } from "./actions.js";
import { h, modal, toast, confirmBox, promptBox, closeTopModal } from "./ui.js";
import { requestFixture, openManualFixture, offerLightTest } from "./lightdialogs.js";

export { openCueDialog, openCueList } from "./cuedialogs.js";
export { openSettings, openSettingsAt } from "./settings.js";
export { openMotionCalibration, requestFixture, openManualFixture, openLightTest } from "./lightdialogs.js";

// ================================================================== add
let preview = null;

export function openAddDialog(query = "") {
  let chosen = null;
  let source = "all";
  const list = h("div.lib-list");
  const search = h("input", { type: "search", placeholder: "Search: brand, model, type…", value: query, autocomplete: "off" });
  const tabs = h("div.lib-tabs",
    h("button", { "aria-selected": "true", dataset: { src: "all" }, title: "Installed, the built-in library, Open Fixture Library, QLC+ and GDTF Share at once" }, "All"),
    h("button", { "aria-selected": "false", dataset: { src: "lib" } }, "Installed"),
    h("button", { "aria-selected": "false", dataset: { src: "open" }, title: "The built-in library + Open Fixture Library + QLC+: thousands of lights, offline" }, "Libraries"),
    h("button", { "aria-selected": "false", dataset: { src: "share" } }, "GDTF Share"));
  const shareNote = h("div.muted.small", { style: { padding: "8px 12px" } });
  const pv = h("div.preview3d", h("div.cap"));
  const title = h("div.pick-title", "Pick a fixture");
  const meta = h("div.pick-meta", "One search covers everything: your installed fixtures, the built-in library, Open Fixture Library, QLC+ and (signed in) GDTF Share.");
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
      h("button.btn", { title: "Ask for a light no library has yet: it opens a filled-in request on the desk's GitHub page", onclick: () => requestFixture(search.value) }, "Request it…"),
      h("span.muted.small.grow", "New fixtures are addressed after the last one and hung where that kind of light goes. Drag them on the stage to move them."),
      h("button.btn", { onclick: () => close() }, "Close"), addBtn],
    onClose: () => { if (preview) { preview.destroy(); preview = null; } },
  });

  preview = new FixturePreview(pv);

  // the fixture shown on the right must be one of the results on the left:
  // a pick left over from before the search was what "Add to stage" added
  const sameItem = (a, b) => !!(a && b) && (a.id ?? a.key ?? a.rid) === (b.id ?? b.key ?? b.rid)
    && a.manufacturer === b.manufacturer && (a.model || a.fixture) === (b.model || b.fixture);
  // nothing found: one click to ask for it
  const askLink = (q) => h("button.linkish", { onclick: () => requestFixture(q) }, "Can't find it? Request it…");
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

  // the light's own 3D when there is one (its GDTF, or the maker's body
  // from GDTF Share); the drawn stand-in only when there isn't
  async function showModel(item) {
    const family = `${item.manufacturer || ""} ${item.model || item.fixture || ""}`;
    const cap = pv.querySelector(".cap");
    const note = h("span.muted", " · looking for its 3D…");
    cap.append(note);
    preview.clear();
    let real = false;
    try {
      const r = await post("/api/fixtures/real_model", { manufacturer: item.manufacturer || "", model: item.model || item.fixture || "",
        source: item.source || "", moving: !!(item.body && item.body.moving), fetch: true });
      if (chosen !== item || !preview) return;
      if (r.ok && r.definition) real = await preview.showReal(r.definition, item.body, family, modelBytes);
    } catch (e) { /* no 3D to be had: the stand-in */ }
    if (chosen !== item || !preview) return;
    note.textContent = real ? " · its own 3D" : " · drawn (no 3D model for this light)";
    if (!real) preview.show(item.body, family);
  }

  function showPick(item) {
    chosen = item;
    right.classList.remove("no-pick");
    [...list.children].forEach((b) => b.classList.toggle("on", b._item === item));
    const b = item.body || {};
    pv.querySelector(".cap").replaceChildren(h("b", b.label || "Fixture"), " · ", b.brand_name || item.manufacturer || "");
    if (preview && item.body) showModel(item);
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
    shareNote.textContent = libs.map((l) => `${l.name}: ${l.fixtures} fixtures (${l.licence}${l.built ? `, updated ${l.built}` : ""})`).join(" · ");
    const rows = d.results || [];
    if (!rows.length) {
      noPick();
      list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } }, "Nothing matches. Try fewer words, or the GDTF Share tab. ", askLink(q)));
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
        r.src === "ofl" ? "OFL" : r.src === "qlc" ? "QLC+" : "Built-in"));
    }
    list.replaceChildren(...rows);
    if (!rows.length) {
      noPick();
      list.replaceChildren(h("div.muted.small", { style: { padding: "14px" } },
        q.length < 2 ? "Type a brand or model (e.g. \u201cfunfetti\u201d, \u201cwave 360\u201d)." : "Nothing matches in the installed fixtures or the libraries. ",
        q.length < 2 ? "" : askLink(q)));
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
      if (r.ok) {
        toast(r.summary || "Added", "ok");
        offerLightTest((r.heads || [])[0]);
      }
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


// ================================================================ shows
export async function openShowMenu(anchor, menuFn) {
  const shows = (state.snap && state.snap.shows) || [];
  const current = (state.snap && state.snap.show_file) || "";
  menuFn(anchor, [
    { label: "New show…", hint: "a template (club, wedding, band, theatre, corporate) or empty", run: openNewShow },
    { label: "Save", hint: "Ctrl+S", run: () => saveShow(current) },
    { label: "Save as…", run: () => saveShow("") },
    { label: "Paperwork…", hint: "light plot, patch sheet, rigging - print / PDF", run: () => window.open("/plot.html", "_blank") },
    { label: "Import MVR plot…", hint: "Vectorworks, Capture, grandMA3: lights patched and placed", run: importMvr },
    { label: "Export as MVR", hint: "the patch and the rigging, for other programs", run: exportMvr },
    "-",
    ...shows.slice(0, 8).map((name) => ({
      label: "Open " + name + (name === current ? "  (open)" : ""),
      run: () => openShowFile(name),
    })),
    shows.length ? { label: `All shows… (${shows.length})`, hint: "find, open, rename, copy, delete", run: openShowFiles }
      : { label: "No saved shows yet", disabled: true, run() {} },
    "-",
    { label: "Ready? check…", hint: "before doors", run: openReadyCheck },
    current ? { label: "Earlier versions…", run: () => openVersions(current) } : null,
    current ? { label: "Export (download)", run: () => exportShow(current) } : null,
  ]);
}

async function openShowFile(name) {
  if (!(await confirmBox("Open show", `Open “${name}”? The current patch, cues and palettes are replaced (the autosave keeps what you had until the next change).`, { ok: "Open" }))) return false;
  const d = await post("/api/console/load", { name }).catch((e) => ({ error: e.message }));
  const r = d.result || {};
  if (d.error || !r.ok) { toast(d.error || r.error, "bad"); return false; }
  toast(r.summary || "Opened", "ok");
  return true;
}

// Every saved show (the menu shows the latest 8): find, open, rename, copy,
// delete.  Deleting keeps the file in the shows folder's .bin.
export async function openShowFiles() {
  const search = h("input", { type: "search", placeholder: "Find a show…", "aria-label": "Find a show", style: { width: "100%" } });
  const list = h("div.show-files");
  let rows = [];
  const when = (t) => new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  const kb = (n) => (n < 1048576 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`);
  async function load() {
    const r = await run("show_files", {}, { silentError: true });
    rows = r.shows || [];
    paint();
  }
  function paint() {
    const q = search.value.trim().toLowerCase();
    const shown = rows.filter((x) => !q || x.name.toLowerCase().includes(q));
    list.replaceChildren(...(shown.length ? shown.map((x) => h("div.show-file" + (x.open ? ".open" : ""),
      h("div.sf-name", h("b", x.name), x.open ? h("span.chip.on", "open") : null,
        h("small.muted", `${when(x.saved)} · ${kb(x.bytes)}${x.versions ? ` · ${x.versions} earlier version${x.versions > 1 ? "s" : ""}` : ""}`)),
      h("div.sf-acts",
        h("button.btn.small.primary", { disabled: x.open, onclick: async () => { if (await openShowFile(x.name)) close(); } }, "Open"),
        h("button.btn.small", { onclick: async () => {
          const nn = await promptBox("Rename show", "New name", x.name, { ok: "Rename" });
          if (nn && nn !== x.name) { const r = await run("show_rename", { name: x.name, new: nn }, { toast: true }); if (r.ok) load(); }
        } }, "Rename"),
        h("button.btn.small", { onclick: async () => {
          const nn = await promptBox("Copy show", "Name of the copy", `${x.name} copy`.slice(0, 40), { ok: "Copy" });
          if (nn) { const r = await run("show_copy", { name: x.name, new: nn }, { toast: true }); if (r.ok) load(); }
        } }, "Copy"),
        h("button.btn.small.danger", { disabled: x.open, title: x.open ? "Open another show first" : "Moves it to the shows folder's .bin", onclick: async () => {
          if (await confirmBox("Delete show", `Delete “${x.name}”? It goes to the shows folder's .bin, where it can still be found.`, { ok: "Delete", danger: true })) {
            const r = await run("show_delete", { name: x.name }, { toast: true }); if (r.ok) load();
          }
        } }, "Delete"))))
      : [h("p.muted", rows.length ? "No show matches." : "No saved shows yet - Save as… keeps the one you're working on.")]));
  }
  search.addEventListener("input", paint);
  const close = modal({ title: "Shows", wide: true, body: h("div", search, list),
    foot: [h("span.grow"), h("button.btn", { onclick: () => close() }, "Close")] });
  load();
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

// MVR: a plot from Vectorworks, Capture, grandMA3, Depence... comes in
// with every light patched (its own GDTF, mode and address) and placed;
// lights hung in a row get a truss.  One Ctrl+Z takes it all back.
function importMvr() {
  const pick = h("input", { type: "file", accept: ".mvr", hidden: true });
  document.body.append(pick);
  pick.addEventListener("change", async () => {
    const file = pick.files && pick.files[0];
    pick.remove();
    if (!file) return;
    const patched = ((state.snap && state.snap.patch) || []).length;
    let replace = false;
    if (patched) {
      const choice = await new Promise((done) => {
        const close = modal({
          title: `Import ${file.name}`,
          body: h("p", `There are ${patched} light(s) in this show already. Add the plot to them, or start over from the plot (the lights and the trusses go, the room is sized to the plot)? One Ctrl+Z undoes either.`),
          foot: [h("button.btn", { onclick: () => { close(); done(null); } }, "Cancel"), h("span.grow"),
            h("button.btn", { onclick: () => { close(); done("add"); } }, "Add to this show"),
            h("button.btn.primary", { onclick: () => { close(); done("new"); } }, "Start from the plot")],
        });
      });
      if (!choice) return;
      replace = choice === "new";
    }
    toast(`Reading ${file.name}…`, "", 2000);
    const t = token();
    const r = await fetch(`/api/mvr/import${replace ? "?replace=1" : ""}`, {
      method: "POST", body: file,
      headers: { "Content-Type": "application/x-jarvis-upload", ...(t ? { "X-Jarvis-Token": t } : {}) },
    });
    const d = await r.json().catch(() => ({ error: `import failed (${r.status})` }));
    if (!d.ok) { toast(d.error || "Nothing could be imported", "bad"); if (!(d.problems || []).length) return; }
    const list = (title, items) => (items && items.length ? h("div", h("b", title), h("ul.small", ...items.map((x) => h("li", x)))) : null);
    modal({
      title: d.ok ? "MVR imported" : "MVR not imported",
      body: h("div",
        h("p", d.summary || d.error || ""),
        d.ok ? h("p.muted.small", "Each light has its own GDTF (with its 3D body when the file has one), its mode and its address from the plot. Ctrl+Z takes the whole import back.") : null,
        list("Changed on the way", d.notes), list("Not imported", d.problems)),
    });
  });
  pick.click();
}

async function exportMvr() {
  const t = token();
  const r = await fetch("/api/mvr/export", { headers: t ? { "X-Jarvis-Token": t } : {} });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    toast(d.error || `export failed (${r.status})`, "bad");
    return;
  }
  const name = (/filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "") || [0, "show.mvr"])[1];
  const url = URL.createObjectURL(await r.blob());
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
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

// ================================================================== help
const KEYS = [
  ["Space / Enter", "GO on the focused playback"], ["B", "back one cue"], ["X", "blackout"],
  ["/ or Ctrl+K", "command bar"], ["Ctrl+Z / Ctrl+Shift+Z", "undo / redo"], ["Ctrl+S", "save the show"],
  ["A / Shift+A", "select all / none"], ["1 … 9", "select fixture 1–9 (Shift adds)"], ["G", "group the selection"],
  ["L", "locate"], ["C", "clear the programmer"], ["R", "record a cue"], ["O", "overwrite the current cue"], ["I", "insert a cue after the current one"], ["D", "delete the current cue"], ["↑ ↓", "intensity ±5 (Shift ±1)"],
  ["T / Shift+T", "tap the tempo / this is beat 1"], ["H / Shift+H", "highlight / solo the selection"], ["F", "frame the selection on stage"], ["Alt+1..9", "switch workspace"], ["Esc", "close / leave full screen"], ["?", "this help"],
];
const SYNTAX = [
  ["1-4 red", "select 1 to 4, colour red"], ["1.3.5 dimmer 70", "select 1, 3 and 5, dimmer 70"], ["all warm white", "every fixture warm white"],
  ["1-4 pan 90", "pan 90° (through each fixture's own range)"], ["group 2 zoom 200", "a group, raw value"],
  ["cue 3 go", "go to cue 3"], ["record", "record a cue"], ["master 60", "grand master"], ["blackout on", "blackout"],
];

export function openHelp() {
  const dl = (rows) => h("dl", ...rows.flatMap(([k, v]) => [h("dt", h("kbd", k)), h("dd", v)]));
  modal({
    title: "Help", wide: true,
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
    foot: [h("span.muted.small", "A light doing the wrong thing? Right-click it → Report a problem with this light."),
      h("button.btn", { onclick: () => { closeTopModal(); startTour(); } }, "Take the tour"),
      h("button.btn", { onclick: () => import("./bugreport.js").then((m) => m.openBugReport()) }, "Report a bug…")],
  });
}

export { patch, selected };
