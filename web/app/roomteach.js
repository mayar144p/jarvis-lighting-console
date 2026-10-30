// Teach the room by pointing: the lights learn where the dance floor, the
// bar, the walls are from a REAL light pointed at them - no tape measure.
// Where a beam lands is worked out from where the light hangs and its Line
// up (app/engine_zones.py), so a lined-up light pointed at the corners of
// the dance floor draws it where it really is.
//   Mark out a zone - point at each corner, "Add this corner", then make it
//   Not here        - point at a wall, the bar staff, the DJ: no beam goes
//                     there again (pointed again nearby, the area grows)
//   Stay in a zone  - these lights' beams stay in the zones picked, whatever
//                     they play (cues, palettes, effects, roams)
import { h, modal, toast } from "./ui.js";
import { run, select } from "./actions.js";
import { state, patch, selected, on } from "./store.js";
import { ZONE_KINDS } from "./venuepanel.js";
import { zoneOutline } from "./zoneshape.js";
import { roomSides } from "./lineup.js";

const movers = () => patch().filter((x) => (x.map || []).includes("pan") && (x.map || []).includes("tilt"));
const venue = () => (state.snap && state.snap.venue) || {};
const NS = "http://www.w3.org/2000/svg";
const s = (tag, attrs = {}, ...kids) => { const el = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); el.append(...kids); return el; };

export function openRoomTeach(tab = "zone") {
  const all = movers();
  if (!all.length) { toast("Add a moving light first - it's what points at the room"); return; }
  const sel = selected();
  let light = (all.find((x) => sel.includes(x.head_no) && x.align) || all.find((x) => x.align) || all.find((x) => sel.includes(x.head_no)) || all[0]).head_no;
  let fine = false;
  let spot = null;                                 // where the beam lands now {x, z, in_room}
  let corners = [];
  let kind = "dancefloor";

  // ---- the light: alone, lit; where its beam lands
  const lightSel = h("select.select", ...all.map((x) => h("option", { value: x.head_no }, `${x.name || "light " + x.head_no}${x.align ? " (lined up)" : ""}`)));
  lightSel.value = String(light);
  const lined = h("span.small");
  const useLight = async () => {
    light = +lightSel.value;
    await select([light]);
    run("highlight", { state: true, solo: true }, { silentError: true });
    const l = patch().find((x) => x.head_no === light) || {};
    lined.replaceChildren(l.align ? h("span.ok-text", "✓ lined up: its beam lands where the desk says")
      : h("span.warn-text", "Not lined up: the spots it marks may be off. ", h("button.linkish.small", {
        onclick: () => import("./lineup.js").then((m) => m.openLineUp([light])) }, "Line it up first")));
    where();
  };
  lightSel.addEventListener("change", useLight);

  let whereT = 0;
  const where = () => {
    clearTimeout(whereT);
    whereT = setTimeout(async () => {
      const r = await run("beam_spot", { head: light }, { silentError: true });
      spot = r.ok ? { x: r.x, z: r.z, in_room: r.in_room } : null;
      spotText.textContent = spot ? `The beam lands at ${spot.x}, ${spot.z} m${spot.in_room ? "" : " - outside the room drawn (a wall?)"}` : (r.error || "");
      draw();
    }, 250);
  };
  const nudge = (axis, dir) => run("nudge", { axis, step: dir * (fine ? 0.002 : 0.02) }).then(where);
  const arrow = (label, axis, dir, cls) => h("button.btn.lineup-arrow" + cls, { "aria-label": label, title: label, onclick: () => nudge(axis, dir) },
    { up: "▲", down: "▼", left: "◀", right: "▶" }[cls.slice(1)]);
  const fineBtn = h("button.btn.small.lineup-fine", { onclick: () => { fine = !fine; fineBtn.classList.toggle("on", fine); fineBtn.textContent = fine ? "Fine ✓" : "Fine"; } }, "Fine");
  const pad = h("div.lineup-pad", arrow("Tilt one way", "tilt", 1, ".up"), arrow("Pan one way", "pan", -1, ".left"), fineBtn,
    arrow("Pan the other way", "pan", 1, ".right"), arrow("Tilt the other way", "tilt", -1, ".down"));
  const spotText = h("p.small.muted");

  // ---- the floor plan: the room, its zones, the corners so far, the beam
  const plan = s("svg", { class: "teach-plan", preserveAspectRatio: "xMidYMid meet" });
  const draw = () => {
    const v = venue(), room = v.room || {};
    const W = +room.width || 16, D = +room.depth || 20, x0 = (+room.cx || 0) - W / 2, z0 = +room.back || 0;
    const pts = [[x0, z0], [x0 + W, z0 + D], ...corners.map((c) => [c.x, c.z]), ...(spot ? [[spot.x, spot.z]] : [])];
    const M = Math.max(1.2, Math.max(W, D) * 0.07);   // room round the edges for their names
    const minX = Math.min(...pts.map((p) => p[0])) - M, maxX = Math.max(...pts.map((p) => p[0])) + M;
    const minZ = Math.min(...pts.map((p) => p[1])) - M, maxZ = Math.max(...pts.map((p) => p[1])) + M;
    plan.setAttribute("viewBox", `${minX} ${minZ} ${maxX - minX} ${maxZ - minZ}`);
    const u = Math.max(maxX - minX, maxZ - minZ) / 60;
    const g = s("g");
    g.append(s("rect", { x: x0, y: z0, width: W, height: D, class: "tp-room" }));
    const sides = s("g", { class: "tp-sides" });
    sides.innerHTML = roomSides(x0, z0, W, D, M);
    g.append(sides);
    for (const z of v.zones || []) {
      g.append(s("polygon", { points: zoneOutline(z, room).map(([a, b]) => `${a},${b}`).join(" "), class: "tp-zone" + (z.kind === "keepout" ? " keepout" : "") }, s("title", {}, z.name || z.kind)));
    }
    if (corners.length > 1) g.append(s("polygon", { points: corners.map((c) => `${c.x},${c.z}`).join(" "), class: "tp-new" }));
    corners.forEach((c, k) => g.append(s("circle", { cx: c.x, cy: c.z, r: u * 0.9, class: "tp-corner" }), s("text", { x: c.x + u, y: c.z - u, "font-size": u * 2.2, class: "tp-n" }, String(k + 1))));
    if (spot) g.append(s("circle", { cx: spot.x, cy: spot.z, r: u * 1.3, class: "tp-beam" }));
    plan.replaceChildren(g);
  };

  // ---- the three jobs
  const pane = h("div.teach-pane");
  const tabs = h("div.seg.teach-tabs");
  const outside = () => corners.filter((c) => !c.in_room);
  const panes = {
    zone: () => {
      const kindSel = h("select.select", ...ZONE_KINDS.filter(([k]) => k !== "keepout").map(([k, l]) => h("option", { value: k }, l)));
      kindSel.value = kind;
      kindSel.addEventListener("change", () => { kind = kindSel.value; });
      const name = h("input", { type: "text", placeholder: "its name (optional)" });
      const fit = outside().length ? h("p.small.warn-text", `${outside().length} corner(s) are outside the room drawn - the room is bigger than the desk thinks. `,
        h("button.btn.small", { onclick: async () => { await run("room_fit", { points: corners.map((c) => [c.x, c.z]) }, { toast: true }); corners = corners.map((c) => ({ ...c, in_room: true })); show("zone"); } }, "Make the room fit")) : null;
      return [h("p.small", "Point the light at each corner of the area in turn (the dance floor, the bar...) and add it. Three corners or more, in order round the edge."),
        h("div.lineup-row", kindSel, name),
        h("div.lineup-big",
          h("button.btn.primary.lineup-go", { onclick: () => {
            if (!spot) { toast("Aim the beam down at the floor first"); return; }
            corners.push({ ...spot }); show("zone"); draw();
          } }, `Add this corner (${corners.length + 1})`),
          h("div.row-btns",
            h("button.btn", { disabled: corners.length < 3, onclick: async () => {
              const label = (ZONE_KINDS.find(([k]) => k === kind) || [kind, kind])[1];
              const r = await run("venue_add", { item: { kind, name: name.value.trim() || label, points: corners.map((c) => [c.x, c.z]) } }, { toast: true });
              if (r.ok) { corners = []; show("zone"); draw(); }
            } }, corners.length < 3 ? `Make the zone (${3 - corners.length} more corner${corners.length === 2 ? "" : "s"})` : "Make the zone"),
            h("button.btn.ghost", { disabled: !corners.length, onclick: () => { corners.pop(); show("zone"); draw(); } }, "Undo corner"))),
        fit];
    },
    keepout: () => {
      const list = (venue().zones || []).filter((z) => z.kind === "keepout");
      return [h("p.small", "Point the light at what a beam must never hit - a wall, a mirror, the bar staff, the DJ's eyes - and press Not here. Pointed again near it, the area grows. Every light keeps off it, whatever it plays."),
        h("div.lineup-big", h("button.btn.primary.lineup-go.danger-go", { onclick: async () => {
          const r = await run("keep_out_here", { head: light }, { toast: true });
          if (r.ok) setTimeout(() => show("keepout"), 300);
        } }, "Not here")),
        list.length ? h("div.teach-list", h("div.small.muted", "Kept out:"), ...list.map((z) => h("div.lineup-row", h("span", z.name || "keep-out"),
          h("button.btn.small.ghost", { onclick: async () => { await run("venue_remove", { id: z.id }, { toast: true }); setTimeout(() => show("keepout"), 300); } }, "Remove")))) : null];
    },
    stay: () => {
      const zones = (venue().zones || []).filter((z) => z.kind !== "keepout");
      if (!zones.length) return [h("p.small", "No zones yet: mark one out first (the dance floor, the bar).")];
      const pickL = new Set(selected().filter((n) => all.some((x) => x.head_no === n)));
      if (!pickL.size) all.forEach((x) => pickL.add(x.head_no));
      const pickZ = new Set([zones[0].id]);
      const chip = (set, key, label, title) => { const b = h("button.chip" + (set.has(key) ? ".on" : ""), { title, onclick: () => { set.has(key) ? set.delete(key) : set.add(key); b.classList.toggle("on", set.has(key)); } }, label); return b; };
      return [h("p.small", "These lights' beams stay inside the zones picked - cues, palettes, effects and roams included. (The keep-out zones hold for every light anyway.)"),
        h("div.small.muted", "Lights"), h("div.lineup-chips", ...all.map((x) => chip(pickL, x.head_no, (x.stay && x.stay.length ? "⌂ " : "") + (x.name || x.head_no), x.stay && x.stay.length ? "stays in a zone now" : ""))),
        h("div.small.muted", "Stay in"), h("div.lineup-chips", ...zones.map((z) => chip(pickZ, z.id, z.name || z.kind))),
        h("div.row-btns",
          h("button.btn.primary", { onclick: () => run("stay_in", { heads: [...pickL], zones: [...pickZ] }, { toast: true }).then(() => setTimeout(() => show("stay"), 300)) }, "Keep them in"),
          h("button.btn.ghost", { onclick: () => run("stay_in", { heads: [...pickL], clear: true }, { toast: true }).then(() => setTimeout(() => show("stay"), 300)) }, "Let them go anywhere"))];
    },
  };
  let cur = tab;
  const show = (k) => {
    cur = k;
    for (const b of tabs.children) b.classList.toggle("on", b.dataset.k === k);
    pane.replaceChildren(...panes[k]().filter(Boolean));
  };
  for (const [k, label] of [["zone", "Mark out a zone"], ["keepout", "Not here (keep out)"], ["stay", "Keep lights in a zone"]]) {
    tabs.append(h("button", { dataset: { k }, onclick: () => show(k) }, label));
  }
  const off = on("snapshot", () => draw());
  const close = modal({
    title: "Teach the room by pointing", wide: true,
    body: h("div.teach",
      h("div.lineup-row", h("span.k", "Light"), lightSel, lined),
      tabs,
      h("div.teach-cols",
        h("div.teach-left", pad, spotText, plan),
        pane)),
    foot: [h("span.muted.small.grow", "Point a lined-up light: where its beam lands is where the desk puts it."), h("button.btn", { onclick: () => close() }, "Close")],
    onClose: () => { if (typeof off === "function") off(); run("highlight", { state: false }, { silentError: true }); },
  });
  show(cur);
  useLight();
}
