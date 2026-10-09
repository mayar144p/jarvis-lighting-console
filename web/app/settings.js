// Settings: output, the venue, MIDI and OSC, controllers, the theme, AI.

import { get, post } from "./api.js";
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
  const usbHint = h("p.muted.small", "Enttec DMX USB Pro and compatible boxes (DMXking ultraDMX…). One box is one universe: universe 1. "
    + "Not in the list? Plug it in, then Re-check network.");
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
    usbField.hidden = usbHint.hidden = !usb;
    findBtn.hidden = usb;
  };
  const showPorts = (net) => {
    const ports = net.usb_ports || [];
    const cur = net.target.mode === "usb" ? net.target.host : "";
    usbPort.replaceChildren(...(ports.length ? ports : [""]).concat(cur && !ports.includes(cur) ? [cur] : [])
      .map((p) => h("option", { value: p, selected: p === cur }, p || "None found")));
  };
  const target = () => mode.value === "usb" ? { mode: "usb", host: usbPort.value } :
    { mode: mode.value, host: mode.value === "auto" ? "" : ip.value.trim(), transport: proto.value };
  mode.addEventListener("change", syncIp);

  const verdict = (net) => {
    const r = net.resolved || {};
    const lines = [h("div", h("b", "Sending to "), h("span.mono", r.transport === "usbpro" ? r.host : `${r.host}:${r.port}`),
      ` · ${r.transport === "usbpro" ? "USB interface, universe 1" : r.transport === "sacn" ? "sACN" : "Art-Net"}`,
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
      h("label.field", h("span", "Protocol"), proto), usbField),
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
      oscOutRow()]],
    ["library", "Fixtures", [
      h("p.muted.small", `${status.fixtures ?? "?"} fixture types installed.`),
      h("div.row-btns", h("button.btn", {
        onclick: async () => {
          const r = await post("/api/fixtures/import", {}).catch((e) => ({ error: e.message }));
          if (r.error) toast(r.error, "bad");
          else toast(`Imported ${(r.imported || []).length} file(s) from fixtures_inbox/` + ((r.errors || []).length ? ` · ${(r.errors || []).length} failed` : ""), (r.errors || []).length ? "bad" : "ok");
        },
      }, "Import fixture files from fixtures_inbox/"), h("span.muted.small", ".gdtf, .qxf or OFL .json"))]],
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
