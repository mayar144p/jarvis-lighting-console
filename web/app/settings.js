// Settings: output, the venue, MIDI and OSC, controllers, the theme, AI.

import { get, post, token } from "./api.js";
import { aiPanel } from "./aisettings.js";
import { connectedControllers } from "./controllers.js";
import { themeNow, setTheme, ACCENTS } from "./theme.js";
import { tipsOn, setTips } from "./help.js";
import { webMidiOn, setWebMidi, webMidiSupported, webMidiInputs, webMidiError } from "./webmidi.js";
import { openNodeMonitor, openMidiMonitor, virtualNodeOn } from "./monitors.js";
import { state } from "./store.js";
import { run } from "./actions.js";
import { h, modal, toast, confirmBox } from "./ui.js";

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
    h("option", { value: "auto", title: "Broadcast on this computer's lighting network" }, "Auto (my lighting network)"),
    h("option", { value: "node" }, "One node: send to its IP"),
    h("option", { value: "broadcast" }, "Broadcast address I choose"),
    h("option", { value: "usb" }, "A USB DMX box"));
  // a USB box shows up as a serial port (COM3, /dev/ttyUSB0): one universe
  const usbPort = h("select.select", { "aria-label": "USB interface port" });
  const usbField = h("label.field", h("span", "USB interface"), usbPort);
  // two kinds of box: one with its own processor (Enttec Pro protocol), or
  // a bare FTDI chip the computer drives itself (the cheap "Open DMX" kind)
  const usbKind = h("select.select", { "aria-label": "Kind of USB box" },
    h("option", { value: "usbpro" }, "Enttec DMX USB Pro and compatible (DMXking ultraDMX…)"),
    h("option", { value: "opendmx" }, "Open DMX / cheap FTDI box (~20 EUR)"));
  const kindField = h("label.field", h("span", "Kind of box"), usbKind);
  const usbHint = h("p.muted.small");
  const syncHint = () => {
    usbHint.textContent = usbKind.value === "opendmx"
      ? "Enttec Open DMX USB and the cheap clones: this computer makes the DMX signal itself (~30 times a second). "
        + "On Windows the box must show up as a COM port: Device Manager -> the USB Serial Converter -> Advanced -> tick Load VCP. "
        + "One box is one universe: universe 1."
      : "Boxes with their own processor: Enttec DMX USB Pro / Mk2, DMXking ultraDMX and clones. One box is one universe: universe 1. "
        + "Not in the list? Plug it in, then Re-check network.";
  };
  usbKind.addEventListener("change", syncHint);
  const ip = h("input", { type: "text", inputmode: "decimal", placeholder: "e.g. 2.0.0.10", spellcheck: "false", autocomplete: "off" });
  const proto = h("select.select",
    h("option", { value: "" }, `Default (${(con.transport || "artnet") === "sacn" ? "sACN" : "Art-Net"})`),
    h("option", { value: "artnet" }, "Art-Net"),
    h("option", { value: "sacn" }, "sACN (E1.31)"));
  const status = h("div.out-status");
  const findBtn = h("button.btn", { onclick: (e) => find(e.currentTarget) }, "Find nodes");
  const adapters = h("div.out-list");
  const nodes = h("div.out-list");
  const syncIp = () => {
    const usb = mode.value === "usb";
    ip.disabled = mode.value === "auto";
    ip.parentElement.style.opacity = ip.disabled ? 0.5 : 1;
    ip.parentElement.hidden = proto.parentElement.hidden = usb;
    usbField.hidden = usbHint.hidden = kindField.hidden = !usb;
    syncHint();
    findBtn.hidden = usb;
  };
  const showPorts = (net) => {
    const ports = net.usb_ports || [];
    const cur = net.target.mode === "usb" ? net.target.host : "";
    usbPort.replaceChildren(...(ports.length ? ports : [""]).concat(cur && !ports.includes(cur) ? [cur] : [])
      .map((p) => h("option", { value: p, selected: p === cur }, p || "None found")));
  };
  const target = () => mode.value === "usb" ? { mode: "usb", host: usbPort.value, transport: usbKind.value } :
    { mode: mode.value, host: mode.value === "auto" ? "" : ip.value.trim(), transport: proto.value };
  mode.addEventListener("change", syncIp);

  const verdict = (net) => {
    const r = net.resolved || {};
    const usbOut = r.transport === "usbpro" || r.transport === "opendmx";
    const lines = [h("div", h("b", "Sending to "), h("span.mono", usbOut ? r.host : `${r.host}:${r.port}`),
      ` · ${r.transport === "opendmx" ? "Open DMX box, universe 1" : usbOut ? "USB interface, universe 1" : r.transport === "sacn" ? "sACN" : "Art-Net"}`,
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
      proto.value = net.target.mode === "usb" ? "" : net.target.transport || "";
      if (net.target.mode === "usb") usbKind.value = net.target.transport || "usbpro";
      routes = { ...(net.target.routes || {}) };
      priority.value = net.target.priority ?? "";
      priField.hidden = (net.resolved || {}).transport !== "sacn";
      showPorts(net); syncIp(); verdict(net); showAdapters(net); drawRoutes();
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
  // a big rig: a universe goes to a node of its own (one node per truss)
  const routesBox = h("div.out-routes");
  const priority = h("input", { type: "number", min: 0, max: 200, step: 1, placeholder: "100" });
  const priField = h("label.field", { title: "When two desks send the same universe, the higher number wins (default 100)" },
    h("span", "sACN priority (0-200)"), priority);
  let routes = {};
  const drawRoutes = () => {
    const rows = Object.entries(routes).sort((a, b) => +a[0] - +b[0]);
    routesBox.replaceChildren(
      h("div.muted.small", "Universes to a node of their own (the rest go to the target above):"),
      ...rows.map(([u, ip]) => h("div.out-row",
        h("span.mono", `Universe ${u}`), h("span", "→"), h("span.mono", ip),
        h("button.btn.small.ghost", { "aria-label": `Stop sending universe ${u} to its own node`,
          onclick: () => { delete routes[u]; apply({ routes }); } }, "Remove"))),
      (() => {
        const uni = h("input", { type: "number", min: 1, max: 4096, placeholder: "universe", "aria-label": "Universe" });
        const node = h("input", { type: "text", inputmode: "decimal", placeholder: "node IP, e.g. 2.0.0.12", "aria-label": "Node IP" });
        return h("div.row-btns", uni, node, h("button.btn.small", { onclick: () => {
          const u = parseInt(uni.value, 10);
          if (!(u >= 1 && u <= 4096) || !node.value.trim()) { toast("A universe (1-4096) and the node's IP", "bad"); return; }
          apply({ routes: { ...routes, [u]: node.value.trim() } });
        } }, "Add"));
      })());
  };
  priority.addEventListener("change", () => apply({ priority: priority.value.trim() }));
  box.append(
    h("div.form-grid.out-grid",
      h("label.field", h("span", "Send DMX to"), mode),
      h("label.field", h("span", "Node / broadcast IP"), ip),
      h("label.field", h("span", "Protocol"), proto), usbField, kindField),
    usbHint,
    h("div.row-btns",
      h("button.btn.primary", { onclick: () => apply(target()) }, "Apply"),
      findBtn,
      h("button.btn", { onclick: refresh }, "Re-check network")),
    status, nodes, adapters,
    h("details.out-more", h("summary.small", "More universes: a node per universe, sACN priority"),
      routesBox, h("div.form-grid", priField)),
    h("p.muted.small", "Saved with the show, so each venue keeps its own node. Changes apply straight away, no restart."),
    h("div.row-btns",
      h("button.btn", { onclick: () => openNodeMonitor() }, virtualNodeOn() ? "Virtual node (on)…" : "Virtual node…"),
      h("span.muted.small", "test the whole output and RDM with no hardware")));
  ip.addEventListener("keydown", (e) => { if (e.key === "Enter") apply({ mode: mode.value === "auto" ? "node" : mode.value, host: ip.value.trim(), transport: proto.value }); });
  status.append(h("div.muted.small", "Checking the network…"));
  refresh();
  return box;
}

/** Settings, opened on one of its pages ("ai", "output"...). */
export function openSettingsAt(tab) {
  try { localStorage.setItem("jarvis.settingsTab", tab); } catch (e) { /* fine */ }
  return openSettings();
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
    h("option", { value: "high" }, "High: sharpest, 8 beams cast shadows (a good graphics card)"),
    h("option", { value: "auto" }, "Medium: adapts to this computer"),
    h("option", { value: "fast" }, "Low: older laptops, no shadows"));
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
  // one section at a time, its name down the side: output first, it is
  // what matters at a gig
  const sections = [
    ["output", "Output", [outputSection(con)]],
    ["venue", "Venue", [
      h("p.muted.small", auto ? "No room drawn yet: the 3D view sizes one around your lights."
        : `${v.name || "Room"}: ${room.width} × ${room.depth} m, ${room.height} m ceiling · ${(v.rigging || []).length} rigging · ${(v.zones || []).length} zones`),
      h("div.row-btns", h("button.btn.primary", { onclick: () => import("./roomdialog.js").then((m) => m.openRoomDialog()) }, "Make the room…"),
        h("span.muted.small", "a shape and its sizes, described in words, a template, a floor plan, or drawn")),
      h("h3", "Start from a template"),
      h("div.form-grid",
        h("label.field", h("span", "Template"), tpl),
        h("div.field", h("span", " "), h("button.btn", {
          onclick: async () => {
            if (!(await confirmBox("Replace the venue", "Start from this template? The room, rigging and zones are replaced; your lights stay where they are (Ctrl+Z undoes it).", { ok: "Replace" }))) return;
            run("venue_template", { name: tpl.value, width: +w.value || null, depth: +d.value || null, height: +ht.value || null }, { toast: true });
          },
        }, "Use template"))),
      h("h3", "Room size"),
      h("div.form-grid",
        h("label.field", h("span", "Width (m)"), w), h("label.field", h("span", "Depth (m)"), d), h("label.field", h("span", "Ceiling (m)"), ht),
        h("div.field", h("span", " "), h("button.btn", {
          onclick: () => run("venue_room", { width: +w.value || null, depth: +d.value || null, height: +ht.value || null }, { toast: true }),
        }, "Resize room")))]],
    ["screen", "Screen & 3D", [
      h("label.check", gigBox, h("span", "Gig mode: big buttons and text everywhere")),
      themeRow(),
      h("h3", "3D view"),
      h("div.form-grid", h("label.field", h("span", "Quality"), quality))]],
    ["midi", "MIDI & OSC", [
      h("p.muted.small", "On the desk computer: " + (midi.enabled ? (midi.open ? `listening to ${midi.device}` : (midi.error || "no MIDI device found")) : "MIDI is off (MIDI_ENABLED=false).")),
      webMidiRow(),
      h("div.row-btns", h("button.btn", { onclick: () => openMidiMonitor() }, "MIDI monitor…"),
        h("span.muted.small", "see what a controller sends and what it did")),
      h("h3", "OSC"),
      oscRow(),
      h("h3", "OSC out"),
      oscOutRow(),
      h("h3", "Stream Deck"),
      streamDeckRow()]],
    ["library", "Fixtures", [
      h("p.muted.small", `${status.fixtures ?? "?"} fixture types installed.`),
      h("div.row-btns", h("button.btn", {
        onclick: async () => {
          const r = await post("/api/fixtures/import", {}).catch((e) => ({ error: e.message }));
          if (r.error) toast(r.error, "bad");
          else toast(`Imported ${(r.imported || []).length} file(s) from fixtures_inbox/` + ((r.errors || []).length ? ` · ${(r.errors || []).length} failed` : ""), (r.errors || []).length ? "bad" : "ok");
        },
      }, "Import fixture files from fixtures_inbox/"), h("span.muted.small", ".gdtf, .qxf or OFL .json"))]],
    ["backup", "Backup", [backupPanel()]],
    ["hue", "Philips Hue", [huePanel()]],
    ["standby", "Backup desk", [standbyPanel()]],
    ["ai", "AI", [aiPanel()]],
  ];
  let last = "output";
  try { last = localStorage.getItem("jarvis.settingsTab") || last; } catch (e) { /* fine */ }
  if (!sections.some(([k]) => k === last)) last = "output";
  const nav = h("nav.set-nav", { role: "tablist", "aria-orientation": "vertical", "aria-label": "Settings sections" });
  const pane = h("div.set-pane");
  const show = (key) => {
    for (const b of nav.children) b.setAttribute("aria-selected", String(b.dataset.k === key));
    const sec = sections.find(([k]) => k === key);
    pane.replaceChildren(h("h2.set-title", sec[1]), ...sec[2]);
    try { localStorage.setItem("jarvis.settingsTab", key); } catch (e) { /* fine */ }
  };
  for (const [k, label] of sections) nav.append(h("button", { role: "tab", dataset: { k }, onclick: () => show(k) }, label));
  show(last);
  const body = h("div.settings", nav, pane);
  modal({ title: "Settings", body, wide: true });
}

// Backup: every saved show copied off this computer (a dead laptop at a
// gig must not take the show with it) - a cloud folder or a USB stick.
function backupPanel() {
  const box = h("div.backup-box");
  const folder = h("input", { type: "text", placeholder: "e.g. C:\\Users\\you\\OneDrive or E:\\", spellcheck: "false", style: { width: "100%" } });
  const places = h("div.chip-row");
  const status = h("div.out-status");
  const paint = (r) => {
    folder.value = r.folder || "";
    status.replaceChildren(r.on
      ? h(r.last_error ? "div.out-bad" : "div.out-ok", r.last_error ? `⚠ The last copy failed: ${r.last_error}` : `✓ Every saved show is copied to ${r.where} · last copy ${r.age}`)
      : h("div.out-bad", "Off: shows live only on this computer."));
    places.replaceChildren(...(r.places || []).map((p) => h("button.chip", { title: p.path, onclick: () => { folder.value = p.path; } }, p.label)));
  };
  const load = async () => { const r = await run("backup", {}, { silentError: true }); if (r.ok) paint(r); };
  const set = async (value) => { const r = await run("backup", { folder: value }, { toast: true }); if (r.ok) paint(r); };
  box.append(
    h("p.muted.small", "Every time a show is saved, it (and its earlier versions) is copied here too, under “Jarvis shows”. "
      + "A OneDrive, Dropbox or Google Drive folder syncs it to the cloud by itself; a USB stick goes home with you."),
    h("label.field", h("span", "Backup folder"), folder),
    places,
    h("div.row-btns",
      h("button.btn.primary", { onclick: () => set(folder.value.trim()) }, "Use this folder"),
      h("button.btn", { onclick: async () => { const r = await run("backup_now", {}, { toast: true }); if (r.ok) load(); } }, "Back up all shows now"),
      h("button.btn", { onclick: () => import("./dialogs.js").then((m) => m.openBackupRestore()) }, "Restore from backup…"),
      h("button.btn.ghost", { onclick: () => set("") }, "Turn off")),
    status);
  load();
  return box;
}

// Philips Hue: smart lamps follow desk lights (a party room, a small bar).
// Pair once with the bridge's round button; each lamp then picks the desk
// light it copies.  The bridge's key never comes to this page.
function huePanel() {
  const box = h("div.hue-box");
  const host = h("input", { type: "text", placeholder: "e.g. 192.168.1.20", spellcheck: "false", autocomplete: "off", style: { width: "180px" } });
  const found = h("div.chip-row");
  const status = h("div.out-status");
  const lampsBox = h("div");
  const heads = () => ((state.snap && state.snap.patch) || []).map((x) => [x.head_no, `${x.head_no} · ${x.name}`]);
  let st = (state.snap && state.snap.hue) || {};
  const paint = () => {
    host.value = host.value || st.host || "";
    status.replaceChildren(st.paired
      ? h(st.error ? "div.out-bad" : "div.out-ok", st.error ? `⚠ ${st.error}` : `✓ Paired with the bridge at ${st.host} · ${st.links} lamp(s) follow the desk`)
      : h("div.out-bad", "Not paired: press the round button on top of the Hue bridge, then Pair within 30 seconds."));
  };
  const refresh = async () => { const r = await run("hue", {}, { silentError: true }); if (r.ok) { st = r.hue; paint(); } };
  const lamps = async () => {
    const r = await run("hue_lights", {}, { toast: true });
    if (!r.ok) return;
    const byLamp = {};
    for (const [hd, lp] of Object.entries(st.map || {})) byLamp[lp] = hd;
    lampsBox.replaceChildren(h("table.hue-lamps",
      h("tr", h("th", "Hue lamp"), h("th", "Follows desk light"), h("th", "")),
      ...r.lamps.map((lp) => {
        const pick = h("select", { "aria-label": `Desk light for ${lp.name}` },
          h("option", { value: "" }, "-"),
          ...heads().map(([n, label]) => h("option", { value: n, selected: String(byLamp[lp.id]) === String(n) }, label)));
        pick.addEventListener("change", async () => {
          const was = byLamp[lp.id];
          const res = pick.value ? await run("hue_link", { head: +pick.value, lamp: lp.id }, { toast: true })
            : was ? await run("hue_link", { head: +was, lamp: "" }, { toast: true }) : { ok: true };
          if (res.ok && res.hue) { st = res.hue; paint(); }
          lamps();
        });
        return h("tr",
          h("td", lp.name, lp.colour ? "" : h("span.muted.small", " (white only)"), lp.reachable ? "" : h("span.muted.small", " · not reachable")),
          h("td", pick),
          h("td", h("button.btn.small", { onclick: () => run("hue_flash", { lamp: lp.id }, { toast: true }) }, "Blink")));
      })));
  };
  box.append(
    h("p.muted.small", "Hue lamps copy a desk light's colour and brightness, so a room with smart bulbs needs no DMX. "
      + "The bridge takes about ten changes a second in all: fine for looks and slow fades, too slow for chases and strobes."),
    h("div.row-btns",
      h("label.field", h("span", "Bridge address"), host),
      h("button.btn", { onclick: async () => {
        const r = await run("hue_find", {}, { toast: true });
        found.replaceChildren(...((r.ok && r.bridges) || []).map((b) => h("button.chip", { onclick: () => { host.value = b.host; } }, `${b.name} ${b.host}`)));
      } }, "Find"),
      h("button.btn.primary", { onclick: async () => {
        const r = await run("hue_pair", { host: host.value.trim() }, { toast: true });
        if (r.ok) { st = r.hue; paint(); lamps(); }
      } }, "Pair")),
    found,
    status,
    h("div.row-btns",
      h("button.btn", { onclick: lamps }, "Show the lamps"),
      h("button.btn.ghost", { onclick: async () => {
        if (!(await confirmBox("Forget the Hue bridge", "Its lamps stop following the desk until you pair again.", { ok: "Forget" }))) return;
        const r = await run("hue", { forget: true }, { toast: true });
        if (r.ok) { st = r.hue; paint(); lampsBox.replaceChildren(); }
      } }, "Forget the bridge")),
    lampsBox);
  paint();
  refresh().then(() => { if (st.paired) lamps(); });
  return box;
}

// A backup desk: this computer follows the main desk live and takes over
// the lights if the main one stops.  The main desk's pairing code stays on
// this computer and never comes back to this page.
function standbyPanel() {
  const box = h("div.standby-box");
  let st = (state.snap && state.snap.standby) || {};
  const host = h("input", { type: "text", placeholder: "e.g. 192.168.1.20:8787", spellcheck: "false", autocomplete: "off", style: { width: "200px" }, value: st.host || "" });
  const code = h("input", { type: "password", placeholder: st.has_code ? "(kept)" : "ABCDE-23456", autocomplete: "off", style: { width: "150px" } });
  const after = h("input", { type: "number", min: 1, max: 30, step: 1, value: st.after || 3, style: { width: "70px" } });
  const auto = h("input", { type: "checkbox", checked: st.auto !== false });
  const status = h("div.out-status");
  const paint = () => {
    status.replaceChildren(
      st.took_over ? h("div.out-bad", `⚠ This desk took over the lights (${st.took_over.why}).` + (st.main_back ? " The main desk is back: only one of them should send - stop the other one's output." : ""))
        : st.following ? h(st.error ? "div.out-bad" : "div.out-ok", st.error ? `⚠ ${st.error}` : `✓ Following ${st.host}${st.show_file ? ` (show “${st.show_file}”)` : ""} · seen ${st.seen_s ?? "?"} s ago · this desk's output is off`)
          : h("div.muted.small", "Not following a main desk."));
  };
  const act = async (name, params) => { const r = await run(name, params, { toast: true }); if (r.ok && r.standby) { st = r.standby; paint(); } return r; };
  box.append(
    h("p.muted.small", "Run a second computer as a spare: it copies the main desk's show and what is live (cues, faders, master, programmer, timeline, buttons) "
      + "twice a second with its own output off. If the main desk stops answering, this one takes over the lights where the main one was. "
      + "On the main desk, allow other computers (the desktop app: Desk → Other computers, phones and tablets) to see its address and pairing code."),
    h("div.row-btns",
      h("label.field", h("span", "Main desk address"), host),
      h("label.field", h("span", "Its pairing code"), code)),
    h("div.row-btns",
      h("label.check", auto, h("span", "Take over by itself when the main desk is quiet for")), after, h("span.muted.small", "seconds")),
    h("div.row-btns",
      h("button.btn.primary", { onclick: () => act("standby", { host: host.value.trim(), ...(code.value.trim() ? { code: code.value.trim() } : {}), after: +after.value || 3, auto: auto.checked }) }, "Follow the main desk"),
      h("button.btn.danger", { onclick: async () => {
        if (await confirmBox("Take over the lights", "This desk's output goes on and it carries on from the main desk's last state. Stop the main desk's output if it is still running.", { ok: "Take over", danger: true })) act("standby_take", {});
      } }, "Take over now"),
      h("button.btn.ghost", { onclick: () => act("standby_stop", {}) }, "Stop following")),
    status);
  paint();
  return box;
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

// The Stream Deck plugin: GO, blackout, buttons, tap and macros on the
// keys; the grand master and playback faders on the dials (Stream Deck +).
// It talks OSC, so OSC in has to be on.
function streamDeckRow() {
  const o = (state.snap && state.snap.osc) || {};
  const download = async () => {
    const t = token();
    const r = await fetch("/api/console/streamdeck_plugin", { headers: t ? { "X-Jarvis-Token": t } : {} });
    if (!r.ok) { toast(`download failed (${r.status})`, "bad"); return; }
    const name = (/filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "") || [0, "com.jarvis.desk.streamDeckPlugin"])[1];
    const url = URL.createObjectURL(await r.blob());
    const a = h("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("Saved. Double-click it to add Jarvis to the Stream Deck app.");
  };
  return h("div",
    h("div.row-btns", h("button.btn", { onclick: download }, "Get the Stream Deck plugin"),
      h("span.muted.small", "Stream Deck app 6.4 or newer, on Windows or Mac")),
    h("p.muted.small", "Double-click the file to install it, then drag GO, Blackout, Desk button, Tap tempo or Macro onto keys, and Grand master or Playback fader onto the dials. "
      + (o.on ? `It talks to OSC in on port ${o.port}: if you change the port, change it in a key's settings too.`
        : "Turn on OSC in above first: the plugin talks to it (port 8000 unless you change it in a key's settings).")
      + " On another computer, put this desk's address in a key's settings."));
}

// OSC out: where the desk's OSC goes (QLab, Resolume, a video server), and
// whether it reports GO, the master, blackout and buttons there.  Saved
// with the show.  A cue sends its own with Actions -> Send OSC.
function oscOutRow() {
  const o = (state.snap && state.snap.osc_out) || {};
  const host = h("input", { type: "text", value: o.host || "", placeholder: "e.g. 192.168.1.20", spellcheck: "false", autocomplete: "off", style: { width: "160px" } });
  const port = h("input", { type: "number", min: 1, max: 65535, value: o.port || 53000, style: { width: "90px" } });
  const fb = h("input", { type: "checkbox", checked: !!o.feedback });
  const note = h("span.muted.small", o.error || (o.host ? `${o.sent || 0} message(s) sent` : "off"));
  const apply = () => run("osc_out", { host: host.value.trim(), port: +port.value || 53000, feedback: fb.checked }, { toast: true });
  host.addEventListener("change", apply);
  port.addEventListener("change", apply);
  fb.addEventListener("change", apply);
  return h("div",
    h("div.row-btns", h("span", "Send to"), host, h("span", "port"), port,
      h("button.btn.small", { title: "Sends /jarvis/hello - see it arrive in QLab / Resolume", onclick: () => run("osc_send", { address: "/jarvis/hello", value: 1 }, { toast: true }) }, "Test"), note),
    h("label.check", fb, h("span", "Tell it what the desk does: /jarvis/go <list> <cue> · /jarvis/master 0-1 · /jarvis/blackout · /jarvis/button <id>")),
    h("p.muted.small", "A cue sends its own: Cue list → ⋯ → Actions… → Send OSC (QLab: /go, Resolume: /composition/layers/1/clips/2/connect 1). QLab listens on 53000, Resolume on 7000."));
}

// MIDI on this device: a controller plugged into the computer the
// browser runs on plays the buttons given its notes.
// Show dark, the accent colour and the panels' brightness (this computer)
function themeRow() {
  const t = themeNow();
  const dark = h("input", { type: "checkbox", checked: t.mode === "showdark" });
  dark.addEventListener("change", () => setTheme({ mode: dark.checked ? "showdark" : "normal" }));
  const bright = h("input", { type: "range", min: 30, max: 100, step: 5, value: Math.round(t.bright * 100), "aria-label": "Panel brightness" });
  bright.addEventListener("input", () => setTheme({ bright: +bright.value / 100 }));
  const sw = h("div.qe-swatches", ...Object.entries(ACCENTS).map(([k, [c]]) => h("button.qe-sw" + (t.accent === k ? ".on" : ""), {
    type: "button", title: k, "aria-label": `Accent ${k}`, style: { background: c },
    onclick: (e) => { setTheme({ accent: k }); sw.querySelectorAll(".qe-sw").forEach((b) => b.classList.toggle("on", b === e.currentTarget)); },
  })));
  const tips = h("input", { type: "checkbox", checked: tipsOn() });
  tips.addEventListener("change", () => setTips(tips.checked));
  return h("div",
    h("h3", "Look"),
    h("label.check", tips, h("span", "Hover help: hold the pointer on any control for what it does")),
    h("label.check", dark, h("span", "Show dark: the panels in dim red for a dark venue (the 3D view keeps its colours)")),
    h("div.form-grid", h("label.field", h("span", "Panel brightness"), bright), h("div.field", h("span", "Accent"), sw)));
}

function webMidiRow() {
  const box = h("input", { type: "checkbox" });
  box.checked = webMidiOn();
  const note = h("span.muted.small", "");
  const show = () => {
    const ins = webMidiInputs();
    const ctl = connectedControllers();
    note.textContent = !webMidiSupported() ? "This browser has no MIDI (use Chrome or Edge)."
      : !box.checked ? "" : webMidiError() || (ins.length ? `Listening to ${ins.join(", ")}` : "No controller plugged in yet.")
        + (ctl.length ? ` · Ready layout: ${ctl.map((c) => c.layout).join(", ")} - pads play the buttons page, faders the playbacks.` : "");
  };
  box.disabled = !webMidiSupported();
  box.addEventListener("change", async () => {
    const ok = await setWebMidi(box.checked);
    if (!ok) box.checked = false;
    show();
  });
  show();
  return h("div", h("label.check", box, h("span", "MIDI in the browser: a controller plugged into the computer this screen runs on plays the buttons")), note,
    h("p.muted.small", "Ready layouts with lit pads and moving faders: Akai APC mini / mk2, APC40 / mkII, Novation Launchpad Mini MK3 / X, Behringer X-Touch (Mackie Control mode). Plug one in - it is recognised by name."));
}
