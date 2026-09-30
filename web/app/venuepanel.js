// Arrange mode's controls: the toolbar (add, draw, floor plan), the
// inspector for whatever is selected, and the floor-plan upload with its
// two-point scale.  Every change is one engine action, so Ctrl+Z undoes it.
import { VenueEditor } from "/js/stage/editor.js";
import { post } from "./api.js";
import { state, on, head as headOf } from "./store.js";
import { run, select } from "./actions.js";
import { $, h, menu, toast, promptBox, confirmBox, typingInField, anyModal, modal } from "./ui.js";

let stage = null;
let editor = null;
let arranging = false;

const RIG_ADD = [
  ["truss", "Truss - horizontal", "A 6 m box truss, flown"],
  ["pole", "Pole - vertical", "A 3 m upright pipe to hang lights on"],
  ["pipe", "Pipe / bar - horizontal", "A 4 m pipe near the ceiling"],
  ["tower", "Tower - vertical truss", "A vertical truss tower on a base"],
  ["ladder", "Ladder", "A flat ladder truss"],
  ["stand", "Stand", "A tripod stand with a T-bar"],
  ["base", "Floor base", "A plate for a light on the floor"],
];
const OBJECT_ADD = [
  ["dj_booth", "DJ booth"], ["bar", "Bar"], ["speaker", "Speaker"], ["sub", "Sub"],
  ["pillar", "Pillar"], ["screen", "LED screen"], ["riser", "Riser"], ["table", "Table"],
  ["balcony", "Balcony"], ["wall", "Wall"], ["mark", "Performer mark"],
];
export const ZONE_KINDS = [
  ["dancefloor", "Dance floor"], ["standing", "Standing"], ["seating", "Seating"], ["bar", "Bar"],
  ["vip", "VIP"], ["dj", "DJ / stage area"], ["foh", "FOH / tech"], ["backstage", "Backstage"],
];

const venue = () => (stage && stage.built && stage.built.venue) || (state.snap && state.snap.venue) || {};
const r2 = (v) => Math.round(v * 100) / 100;

function where() {
  // new things appear in front of the stage, in the middle of the room
  const R = stage.room;
  return { x: R.cx || 0, z: Math.min(R.z1 - 1, (stage.stageFront || 0) + 2), h: R.h };
}

function defaults(kind) {
  const { x, z, h: H } = where();
  switch (kind) {
    case "truss": return { a: [x - 3, r2(H - 0.8), z], b: [x + 3, r2(H - 0.8), z] };
    case "pipe": return { a: [x - 2, r2(H - 0.3), z], b: [x + 2, r2(H - 0.3), z] };
    case "tower": case "ladder": return { a: [x, 0, z], b: [x, r2(Math.min(4, H - 0.5)), z] };
    case "stand": return { a: [x, 0, z], b: [x, 2.6, z] };
    case "pole": return { a: [x, 0, z], b: [x, r2(Math.min(3, H - 0.3)), z] };
    case "base": return { a: [x, 0, z], b: [x, 0, z] };
    case "dj_booth": return { x, z, w: 2.5, d: 0.9, h: 1.05 };
    case "bar": return { x, z, w: 6, d: 0.8, h: 1.1 };
    case "speaker": return { x, z, w: 0.7, d: 0.7, h: 1.6 };
    case "sub": return { x, z, w: 1.2, d: 0.9, h: 0.8 };
    case "pillar": return { x, z, w: 0.5, d: 0.5, h: H };
    case "screen": return { x, z: z - 1, w: 4, d: 0.12, h: 2.25, y: 1.2 };
    case "riser": return { x, z, w: 2, d: 2, h: 0.4 };
    case "table": return { x, z, w: 1.6, d: 1.6, h: 0.75 };
    case "balcony": return { x, z, w: 6, d: 2, h: 0.25, y: 3 };
    case "wall": return { x, z, w: 4, d: 0.15, h: Math.min(3, H) };
    case "mark": return { x, z: Math.max(0.5, (stage.stageFront || 2) - 1.5), w: 0.5, d: 0.5, h: 0.02, y: 0 };
    default: return { x, z };
  }
}

async function addItem(kind, label) {
  // a pole is a pipe standing up: its two ends differ in height
  const item = { kind: kind === "pole" ? "pipe" : kind, name: (label || "").replace(/ - .*/, ""), ...defaults(kind) };
  const r = await run("venue_add", { item }, { toast: true });
  if (r.ok && r.id) setTimeout(() => editor.select({ type: "item", id: r.id }), 250);
}

// ------------------------------------------------------------ floor plan
async function fileToImage(file) {
  if (/\.pdf$/i.test(file.name) || file.type === "application/pdf") {
    const pdfjs = await import("/vendor/pdfjs/pdf.min.mjs");
    pdfjs.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.min.mjs";
    const doc = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise;
    const page = await doc.getPage(1);
    const base = page.getViewport({ scale: 1 });
    const scale = Math.min(4, 2400 / Math.max(base.width, base.height));
    const vp = page.getViewport({ scale });
    const c = document.createElement("canvas");
    c.width = Math.round(vp.width);
    c.height = Math.round(vp.height);
    const g = c.getContext("2d");
    g.fillStyle = "#fff";
    g.fillRect(0, 0, c.width, c.height);
    await page.render({ canvasContext: g, viewport: vp }).promise;
    return { canvas: c, type: "image/png" };
  }
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = () => rej(new Error("that file is not an image or a PDF"));
      i.src = url;
    });
    const k = Math.min(1, 3000 / Math.max(img.width, img.height));
    const c = document.createElement("canvas");
    c.width = Math.round(img.width * k);
    c.height = Math.round(img.height * k);
    c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
    return { canvas: c, type: /png/i.test(file.type) ? "image/png" : "image/jpeg" };
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function uploadPlan(file) {
  toast("Reading the floor plan…");
  let got;
  try {
    got = await fileToImage(file);
  } catch (err) {
    toast("Could not read that file: " + (err.message || err), "bad");
    return;
  }
  const data = got.canvas.toDataURL(got.type, 0.9);
  let res;
  try {
    res = await post("/api/console/underlay", { data });
  } catch (err) {
    toast(err.message || String(err), "bad");
    return;
  }
  const R = stage.room;
  const width = R.w || 20;
  await run("venue_underlay", {
    id: res.id, x: R.cx || 0, z: (R.z0 + R.z1) / 2, width,
    aspect: got.canvas.width / got.canvas.height, opacity: 0.65, show: true, rot: 0,
  });
  stage.view("top");
  toast("Floor plan placed. Now set its scale: click two points whose real distance you know.", "ok", 6000);
  setTimeout(() => startMeasure(), 700);
}

function startMeasure() {
  if (!venue().underlay) { toast("Upload a floor plan first", "bad"); return; }
  hint("Set the scale: click the first point on the plan, then the second (Esc cancels).");
  editor.startDraw("measure");
}

async function onMeasure(len, pts) {
  hint(null);
  if (len < 0.05) return;
  const real = await promptBox("Floor plan scale", `You measured ${len.toFixed(2)} m on the plan. What is the real distance between those points, in metres?`, "");
  const m = parseFloat(String(real || "").replace(",", "."));
  if (!(m > 0)) return;
  const u = venue().underlay;
  const k = m / len;
  // scale about the first point, so it stays where it was clicked
  const [px, pz] = pts[0];
  await run("venue_underlay", {
    width: r2(u.width * k), x: r2(px + (u.x - px) * k), z: r2(pz + (u.z - pz) * k),
  }, { toast: `Scale set: ${m} m. Trace the room with "Draw room".` });
}

// ------------------------------------------------------------ drawing
async function onDrawn(kind, pts, opts) {
  hint(null);
  if (kind === "outline") {
    const heightNow = (venue().room || {}).height || 5;
    await run("venue_room", { outline: pts, height: heightNow }, { toast: "Room outline traced" });
  } else if (kind === "zone") {
    const r = await run("venue_add", { item: { kind: opts.kind, name: opts.label || "", points: pts } }, { toast: true });
    if (r.ok && r.id) setTimeout(() => editor.select({ type: "item", id: r.id }), 250);
  }
}

function hint(text) {
  const box = $("#draw-hint");
  box.hidden = !text;
  if (text) box.textContent = text;
}

function drawRoom() {
  stage.view("top");
  hint("Draw the room: click each corner of the walls. Click the first corner (or press Enter) to close it. Shift squares the line; Backspace removes a point.");
  editor.startDraw("outline");
}

function drawZone(kind, label) {
  stage.view("top");
  stage.setOptions({ zones: true });
  $("#zones-btn").classList.add("on");
  hint(`Draw the ${label.toLowerCase()}: click its corners, then click the first one again (or press Enter).`);
  editor.startDraw("zone", { kind, label });
}

// ------------------------------------------------------------ inspector
function num(value, onchange, opts = {}) {
  const el = h("input.num", { type: "number", step: opts.step || 0.05, value: value ?? "", min: opts.min, max: opts.max });
  el.addEventListener("change", () => { if (el.value !== "") onchange(+el.value); });
  return el;
}

function field(label, ...ctl) {
  return h("label.vi-field", h("span", label), ...ctl);
}

function rigOptions(current) {
  const s = h("select.select.small");
  s.append(h("option", { value: "" }, "Free-standing"));
  for (const r of venue().rigging || []) {
    s.append(h("option", { value: r.id }, (r.name || r.kind) + " · " + r.kind));
  }
  s.value = current || "";
  return s;
}

function renderInspector(sel) {
  const box = $("#venue-inspector");
  if (!arranging || !sel) { box.hidden = true; return; }
  box.hidden = false;
  const body = [];
  const del = (id, what) => h("button.btn.small.danger", {
    onclick: async () => { await run("venue_remove", { id }, { toast: true }); editor.select(null); },
  }, "Delete " + what);
  const s = sel.type === "vertex" ? sel.parent : sel;
  if (s.type === "light") {
    const hd = headOf(s.head);
    if (!hd) { box.hidden = true; return; }
    const moving = hd.body && hd.body.moving;
    const mount = hd.mount || {};
    const rigSel = rigOptions(mount.rig);
    rigSel.addEventListener("change", () => run("set_place", rigSel.value
      ? { head: s.head, rig: rigSel.value } : { head: s.head, rig: "", y: hd.y }, { silentError: false }));
    const along = h("input", { type: "range", min: 0, max: 1, step: 0.01, value: mount.t ?? 0.5, disabled: !mount.rig });
    along.addEventListener("change", () => run("set_place", { head: s.head, rig: mount.rig, t: +along.value }));
    const stance = h("div.seg.small",
      ...["hang", "stand"].map((k) => h("button" + ((hd.stance || (hd.kind === "truss" ? "hang" : "stand")) === k ? ".on" : ""), {
        onclick: () => run("set_place", { head: s.head, stance: k, ...(mount.rig ? { rig: mount.rig, t: mount.t } : {}) }),
      }, k === "hang" ? "Hang" : "Stand")));
    body.push(h("div.vi-title", `#${hd.head_no} ${hd.name || hd.model}`),
      h("div.vi-sub", (hd.body && hd.body.label) || hd.model),
      h("div.vi-row", field("X", num(hd.x, (v) => run("set_place", { head: s.head, x: v }))),
        field("Y", num(hd.y, (v) => run("set_place", { head: s.head, y: v }), { min: 0 })),
        field("Z", num(hd.z, (v) => run("set_place", { head: s.head, z: v })))),
      field("On", rigSel),
      field("Along", along),
      field("Mount", stance));
    if (!moving) {
      const rot = hd.rot || null;
      body.push(h("div.vi-row",
        field("Aim yaw°", num(rot ? rot[0] : "", (v) => run("set_place", { head: s.head, rot: [v, rot ? rot[1] : -45] }), { step: 5 })),
        field("Tilt°", num(rot ? rot[1] : "", (v) => run("set_place", { head: s.head, rot: [rot ? rot[0] : 180, v] }), { step: 5 })),
        h("button.btn.small.ghost", { title: "Let the visualiser aim it at the stage", onclick: () => run("set_place", { head: s.head, rot: "auto" }) }, "Auto")));
      body.push(h("p.muted.small", "Rotate (E) to aim it with the gizmo."));
    }
    const sel = (state.snap && state.snap.selected) || [];
    body.push(h("div.row-btns",
      h("button.btn.small", { onclick: () => run("set_place", { head: s.head, x: hd.x, y: hd.y, z: hd.z, snap: true }, { toast: true }) }, "Snap to nearest rig"),
      mount.rig && sel.length > 1 ? h("button.btn.small", {
        onclick: () => run("attach_heads", { heads: sel, rig: mount.rig }, { toast: true }),
      }, `Spread ${sel.length} selected here`) : null));
  } else if (s.type === "rig") {
    const found = (venue().rigging || []).find((r) => r.id === s.id);
    if (!found) { box.hidden = true; return; }
    const upd = (changes) => run("venue_update", { id: s.id, changes });
    const len = Math.hypot(found.b[0] - found.a[0], found.b[1] - found.a[1], found.b[2] - found.a[2]);
    const riders = (state.snap.patch || []).filter((x) => (x.mount || {}).rig === s.id).length;
    const name = h("input", { type: "text", value: found.name || "", placeholder: found.kind });
    name.addEventListener("change", () => upd({ name: name.value }));
    const midY = (found.a[1] + found.b[1]) / 2;
    const sel = (state.snap && state.snap.selected) || [];
    body.push(h("div.vi-title", found.name || found.kind), h("div.vi-sub", `${found.kind} · ${len.toFixed(2)} m · ${riders} light${riders === 1 ? "" : "s"}`),
      field("Name", name),
      h("div.vi-row", h("span.vi-k", "End A"), ...[0, 1, 2].map((i) => num(found.a[i], (v) => { const a = [...found.a]; a[i] = v; upd({ a }); }))),
      h("div.vi-row", h("span.vi-k", "End B"), ...[0, 1, 2].map((i) => num(found.b[i], (v) => { const b = [...found.b]; b[i] = v; upd({ b }); }))),
      field("Height", num(r2(midY), (v) => {
        const d = v - midY;
        upd({ a: [found.a[0], r2(found.a[1] + d), found.a[2]], b: [found.b[0], r2(found.b[1] + d), found.b[2]] });
      }, { min: 0 })),
      h("p.muted.small", "Drag the yellow ends to change its length and angle."),
      h("div.row-btns",
        sel.length ? h("button.btn.small", { onclick: () => run("attach_heads", { heads: sel, rig: s.id }, { toast: true }) }, `Put ${sel.length} selected light${sel.length === 1 ? "" : "s"} on it`) : null,
        del(s.id, found.kind)));
  } else if (s.type === "object") {
    const o = (venue().objects || []).find((x) => x.id === s.id);
    if (!o) { box.hidden = true; return; }
    const upd = (changes) => run("venue_update", { id: s.id, changes });
    const name = h("input", { type: "text", value: o.name || "", placeholder: o.kind });
    name.addEventListener("change", () => upd({ name: name.value }));
    body.push(h("div.vi-title", o.name || o.kind.replace("_", " ")),
      field("Name", name),
      h("div.vi-row", field("X", num(o.x, (v) => upd({ x: v }))), field("Z", num(o.z, (v) => upd({ z: v }))), field("Up", num(o.y, (v) => upd({ y: v }), { min: 0 }))),
      h("div.vi-row", field("W", num(o.w, (v) => upd({ w: v }), { min: 0.05 })), field("D", num(o.d, (v) => upd({ d: v }), { min: 0.05 })), field("H", num(o.h, (v) => upd({ h: v }), { min: 0.01 }))),
      field("Rotate°", num(o.rot, (v) => upd({ rot: v }), { step: 15 })),
      h("div.row-btns", del(s.id, o.kind === "mark" ? "mark" : "object")));
  } else if (s.type === "zone") {
    const z = (venue().zones || []).find((x) => x.id === s.id);
    if (!z) { box.hidden = true; return; }
    const upd = (changes) => run("venue_update", { id: s.id, changes });
    const kind = h("select.select.small", ...ZONE_KINDS.map(([k, l]) => h("option", { value: k }, l)));
    kind.value = z.kind;
    kind.addEventListener("change", () => upd({ kind: kind.value }));
    const name = h("input", { type: "text", value: z.name || "", placeholder: "name" });
    name.addEventListener("change", () => upd({ name: name.value }));
    const dens = h("input", { type: "range", min: 0, max: 1, step: 0.05, value: z.density >= 0 ? z.density : 0.5 });
    dens.addEventListener("change", () => upd({ density: +dens.value }));
    body.push(h("div.vi-title", z.name || z.kind), field("Name", name), field("Kind", kind), field("Crowd", dens),
      h("p.muted.small", "Drag the yellow corners to reshape it."),
      h("div.row-btns", del(s.id, "zone")));
  } else if (s.type === "stage") {
    const st = venue().stage;
    if (!st) { box.hidden = true; return; }
    const upd = (changes) => run("venue_stage", changes);
    body.push(h("div.vi-title", "Stage"),
      h("div.vi-row", field("X", num(st.x, (v) => upd({ x: v }))), field("Back Z", num(st.z, (v) => upd({ z: v })))),
      h("div.vi-row", field("W", num(st.width, (v) => upd({ width: v }), { min: 0.5 })), field("D", num(st.depth, (v) => upd({ depth: v }), { min: 0.5 })), field("H", num(st.height, (v) => upd({ height: v }), { min: 0 }))),
      h("div.row-btns", h("button.btn.small.danger", { onclick: () => { run("venue_stage", { remove: true }, { toast: true }); editor.select(null); } }, "Remove stage")));
  } else if (s.type === "underlay") {
    const u = venue().underlay;
    if (!u) { box.hidden = true; return; }
    const op = h("input", { type: "range", min: 0.1, max: 1, step: 0.05, value: u.opacity });
    op.addEventListener("change", () => run("venue_underlay", { opacity: +op.value }));
    body.push(h("div.vi-title", "Floor plan"),
      h("div.vi-sub", `${u.width} m wide`),
      field("Opacity", op),
      h("div.vi-row", field("Width m", num(u.width, (v) => run("venue_underlay", { width: v }), { min: 0.5, step: 0.5 })),
        field("Rotate°", num(u.rot, (v) => run("venue_underlay", { rot: v }), { step: 1 }))),
      h("div.row-btns",
        h("button.btn.small", { onclick: startMeasure }, "Set scale…"),
        h("button.btn.small", { onclick: drawRoom }, "Trace room"),
        h("button.btn.small.danger", { onclick: () => { run("venue_underlay", { remove: true }); editor.select(null); } }, "Remove")));
  }
  box.replaceChildren(...body.filter(Boolean));
}

// ------------------------------------------------------------ toolbar
function openAdd(btn) {
  menu(btn, [
    ...RIG_ADD.map(([k, l, hint]) => ({ label: l, hint, run: () => addItem(k, l) })),
    "-",
    ...OBJECT_ADD.map(([k, l]) => ({ label: l, run: () => addItem(k, l) })),
    "-",
    { label: "Stage deck", disabled: !!venue().stage, run: () => run("venue_stage", { x: 0, z: 0, width: 8, depth: 4, height: 0.6 }, { toast: true }) },
  ]);
}

function openZones(btn) {
  menu(btn, ZONE_KINDS.map(([k, l]) => ({ label: l, run: () => drawZone(k, l) })));
}

function openPlan(btn) {
  const u = venue().underlay;
  menu(btn, [
    { label: u ? "Replace floor plan…" : "Upload floor plan…", hint: "Image or PDF", run: () => $("#plan-file").click() },
    { label: "Set scale…", disabled: !u, run: startMeasure },
    { label: "Trace room outline", run: drawRoom },
    { label: u && u.show !== false ? "Hide plan" : "Show plan", disabled: !u, run: () => run("venue_underlay", { show: !(u && u.show !== false) }) },
    { label: "Select plan (move / rotate)", disabled: !u, run: () => editor.select({ type: "underlay" }) },
  ]);
}

// ------------------------------------------------------------ My venues
// A venue saved on its own - room, rigging, zones, objects and (if wanted)
// the lights hung in it - to open again at the next gig there.
const venues = () => (state.snap && state.snap.venues) || [];

function openVenues(btn) {
  const list = venues();
  menu(btn, [
    { label: "Save this venue…", hint: "room, rigging, zones and the lights", run: saveVenue },
    ...(list.length ? ["-"] : []),
    ...list.map((v) => ({
      label: `Open ${v.name}`,
      hint: `${v.shape === "custom" ? "custom shape" : "rectangle"} · ${v.rigging} rigging · ${v.lights} light(s)`,
      run: () => openVenue(v),
    })),
    ...(list.length ? ["-", ...list.map((v) => ({ label: `Delete ${v.name}`, danger: true, run: async () => {
      if (await confirmBox("Delete venue", `Delete the saved venue “${v.name}”? The show you have open is not changed.`, { ok: "Delete", danger: true })) {
        run("venue_delete", { name: v.name }, { toast: true });
      }
    } }))] : []),
  ]);
}

async function saveVenue() {
  const cur = venue().name || "";
  const name = await promptBox("Save venue", "Venue name", cur, { ok: "Save", placeholder: "e.g. Club Nova" });
  if (!name) return;
  const lights = (state.snap && state.snap.patch || []).length;
  const withLights = lights ? await confirmBox("Save the lights too?",
    `Keep the ${lights} light(s) hung in ${name} - their addresses and positions - with the venue? (Choose Cancel to save just the room and rigging.)`,
    { ok: "Save with lights" }) : false;
  run("venue_save", { name, lights: withLights }, { toast: true });
}

function openVenue(v) {
  const close = modal({
    title: `Open ${v.name}`,
    body: h("p", { style: { margin: 0 } }, v.lights
      ? `${v.name} was saved with ${v.lights} light(s). Open the room and its lights (replaces your patch), or just the room and rigging?`
      : `Replace this room, rigging and zones with ${v.name}? Your lights stay as they are.`),
    foot: [
      h("button.btn", { onclick: () => close() }, "Cancel"),
      v.lights ? h("button.btn", { onclick: () => { run("venue_open", { name: v.name, lights: false }, { toast: true }); close(); } }, "Room only") : null,
      h("button.btn.primary", { onclick: () => { run("venue_open", { name: v.name, lights: true }, { toast: true }); close(); } },
        v.lights ? "Room and lights" : "Open"),
    ],
  });
}

// At start-up, on an empty desk with saved venues: "Where are you playing
// tonight?" - once per browser session, never over a show in progress.
function venuePicker() {
  const s = state.snap || {};
  const list = venues();
  if (!list.length || (s.patch || []).length || s.show_file || anyModal()) return false;
  try {
    if (sessionStorage.getItem("jarvis.venuepick")) return true;
    sessionStorage.setItem("jarvis.venuepick", "1");
  } catch (e) { /* ask anyway */ }
  const close = modal({
    title: "Where are you playing tonight?",
    body: h("div.vp-list",
      ...list.map((v) => h("button.vp-item", { onclick: () => { close(); openVenue(v); } },
        h("b", v.name),
        h("small", `${v.shape === "custom" ? "custom shape" : "rectangle"} · ${v.rigging} rigging · ${v.lights} light(s)`)))),
    foot: [h("span.muted.small", "Or start fresh: Settings → Venue has room templates."), h("span.grow"),
      h("button.btn", { onclick: () => close() }, "Start empty")],
  });
  return true;
}

export function setArranging(on) {
  arranging = !!on;
  document.body.classList.toggle("arranging", arranging);
  $("#arrange-btn").classList.toggle("on", arranging);
  $("#venue-tools").hidden = !arranging;
  stage.setEditing(arranging, editor);
  if (!arranging) { hint(null); renderInspector(null); }
  $("#stage-hint").textContent = arranging
    ? "Arrange: click a light, truss, object or zone to move it · W move · E rotate · Del delete · Esc done"
    : "Drag to orbit · right-drag to pan · scroll to zoom · click a light to select";
}

export function isArranging() { return arranging; }

export function initVenuePanel(theStage) {
  const offPick = on("snapshot", () => { offPick(); venuePicker(); });   // the first snapshot only
  stage = theStage;
  editor = new VenueEditor(stage, {
    onSelect: (sel) => renderInspector(sel),
    pickLight: (head, ev) => select([head], { add: ev.shiftKey || ev.ctrlKey || ev.metaKey }),
    selectedHeads: () => (state.snap && state.snap.selected) || [],
    moveLights: (moves, snap) => run("place_many", { moves, rig: snap ? snap.rig : null }, snap ? { toast: true } : {}),
    moveLight: (head, changes) => run("set_place", { head, ...changes }),
    updateItem: (id, changes) => run("venue_update", { id, changes }),
    updateStage: (changes) => run("venue_stage", changes),
    updateUnderlay: (changes) => run("venue_underlay", changes),
    onMeasure,
    onDrawn,
    onDrawProgress: (kind, n, len) => {
      if (!kind) return;
      const box = $("#draw-hint");
      const base = box.dataset.base || box.textContent;
      box.dataset.base = base;
      box.textContent = base + (len ? `  ·  ${len.toFixed(2)} m` : "");
    },
  });
  window.jarvisEditor = editor;

  $("#arrange-btn").addEventListener("click", () => setArranging(!arranging));
  $("#vt-move").addEventListener("click", () => { editor.setMode("translate"); modeButtons(); });
  $("#vt-rotate").addEventListener("click", () => { editor.setMode("rotate"); modeButtons(); });
  $("#vt-add").addEventListener("click", (e) => openAdd(e.currentTarget));
  $("#vt-truss").addEventListener("click", () => addItem("truss", "Truss"));
  $("#vt-pole").addEventListener("click", () => addItem("pole", "Pole"));
  $("#vt-pipe").addEventListener("click", () => addItem("pipe", "Pipe"));
  $("#vt-venues").addEventListener("click", (e) => openVenues(e.currentTarget));
  $("#vt-room").addEventListener("click", drawRoom);
  $("#vt-zone").addEventListener("click", (e) => openZones(e.currentTarget));
  $("#vt-plan").addEventListener("click", (e) => openPlan(e.currentTarget));
  $("#vt-done").addEventListener("click", () => setArranging(false));
  $("#plan-file").addEventListener("change", (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = "";
    if (f) uploadPlan(f);
  });
  const modeButtons = () => {
    $("#vt-move").classList.toggle("on", editor.mode === "translate");
    $("#vt-rotate").classList.toggle("on", editor.mode === "rotate");
  };
  modeButtons();

  document.addEventListener("keydown", (ev) => {
    if (!arranging || typingInField(ev) || anyModal()) return;
    if (editor.drawing) return;                      // the editor takes its own keys
    const k = ev.key.toLowerCase();
    if (k === "w") { editor.setMode("translate"); modeButtons(); ev.stopImmediatePropagation(); }
    else if (k === "e") { editor.setMode("rotate"); modeButtons(); ev.stopImmediatePropagation(); }
    else if ((ev.key === "Delete" || ev.key === "Backspace") && editor.sel) {
      const s = editor.sel.type === "vertex" ? editor.sel.parent : editor.sel;
      if (s.type === "rig" || s.type === "object" || s.type === "zone") {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        confirmBox("Delete", "Delete this from the venue? Ctrl+Z brings it back.", { ok: "Delete", danger: true })
          .then((yes) => { if (yes) { run("venue_remove", { id: s.id }, { toast: true }); editor.select(null); } });
      }
    } else if (ev.key === "Escape") {
      if (editor.sel) editor.select(null); else setArranging(false);
      ev.stopImmediatePropagation();
    }
  }, true);

  // keep the inspector in step with the engine
  on("snapshot", () => { if (arranging && editor.sel) renderInspector(editor.sel); });
}
