// Make the room: drawing it is one way among several.  A shape with its
// sizes typed in, a room described in words (read offline, or by the AI
// when there is a key), a template, a floor plan to trace, or drawing the
// walls.  Every way shows the room before it is made, drawn from exactly
// what the engine will build (venue_preview), so there is no second copy
// of the shape maths here.
import { act, post } from "./api.js";
import { run } from "./actions.js";
import { state } from "./store.js";
import { h, modal, toast } from "./ui.js";

const SHAPES = [
  ["rectangle", "Rectangle"], ["l", "L-shape"], ["t", "T-shape"], ["u", "U-shape"],
  ["octagon", "Octagon"], ["round", "Round / oval"], ["wedge", "Fan / wedge"],
];
const KINDS = [["club", "Club"], ["small_club", "Small club / bar"], ["warehouse", "Warehouse"], ["concert", "Concert"],
  ["theatre", "Theatre"], ["ballroom", "Ballroom / event"], ["outdoor", "Outdoor"]];
const TEMPLATES = [["club", "Club", "16 × 22 m"], ["small_club", "Small club / bar", "10 × 14 m"], ["warehouse", "Warehouse rave", "30 × 40 m"],
  ["concert", "Concert stage", "24 × 32 m"], ["theatre", "Theatre", "16 × 26 m"], ["ballroom", "Ballroom / event", "20 × 24 m"],
  ["outdoor", "Outdoor stage", "24 × 30 m"]];
const EXAMPLES = [
  "a 12 x 8 m club, bar on the left, DJ booth on a 40 cm riser",
  "L-shaped warehouse 30 by 20 m, 8 m ceiling, 4 pillars, stage 10 x 5 m at the back",
  "small bar 40 ft x 25 ft, 3.2 m ceiling, DJ in the corner, no truss",
  "round ballroom 24 m across, 6 m high, tables, balcony at the back, screen",
];
// thumbnails for the shape cards (a 40 x 40 box, stage end at the top)
const THUMB = {
  rectangle: "4,6 36,6 36,34 4,34", l: "4,6 36,6 36,20 22,20 22,34 4,34", t: "4,6 36,6 36,17 27,17 27,34 13,34 13,17 4,17",
  u: "4,6 36,6 36,34 26,34 26,21 14,21 14,34 4,34", octagon: "12,6 28,6 36,14 36,26 28,34 12,34 4,26 4,14",
  round: [...Array(20)].map((_, i) => `${20 + 16 * Math.cos(i * Math.PI / 10)},${20 + 14 * Math.sin(i * Math.PI / 10)}`).join(" "),
  wedge: "13,6 27,6 36,34 4,34",
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/** The plan of a venue as SVG (top-down, stage end at the top). */
export function planSvg(p, { w = 420, hgt = 300 } = {}) {
  if (!p || !p.room) return "";
  const pts = p.room.outline && p.room.outline.length ? p.room.outline
    : [[-p.room.width / 2, p.room.back], [p.room.width / 2, p.room.back], [p.room.width / 2, p.room.back + p.room.depth], [-p.room.width / 2, p.room.back + p.room.depth]];
  const xs = pts.map((q) => q[0]), zs = pts.map((q) => q[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), z0 = Math.min(...zs), z1 = Math.max(...zs);
  const pad = 26;
  const k = Math.min((w - 2 * pad) / Math.max(1, x1 - x0), (hgt - 2 * pad) / Math.max(1, z1 - z0));
  const ox = (w - (x1 - x0) * k) / 2, oz = (hgt - (z1 - z0) * k) / 2;
  const X = (x) => (ox + (x - x0) * k).toFixed(1), Z = (z) => (oz + (z - z0) * k).toFixed(1);
  const poly = (q) => q.map(([a, b]) => `${X(a)},${Z(b)}`).join(" ");
  const ZONE = { dancefloor: "#38bdf8", bar: "#f59e0b", dj: "#a78bfa", seating: "#22c55e", stage: "#94a3b8" };
  let s = `<svg viewBox="0 0 ${w} ${hgt}" class="room-plan" role="img" aria-label="Room plan">`;
  s += `<polygon points="${poly(pts)}" fill="#141a24" stroke="#7f8aa0" stroke-width="2"/>`;
  for (const zn of p.zones || []) {
    const c = ZONE[zn.kind] || "#64748b";
    s += `<polygon points="${poly(zn.points)}" fill="${c}" fill-opacity=".14" stroke="${c}" stroke-opacity=".6" stroke-dasharray="4 3"/>`;
  }
  if (p.stage) {
    const st = p.stage;
    s += `<rect x="${X(st.x - st.width / 2)}" y="${Z(st.z)}" width="${(st.width * k).toFixed(1)}" height="${(st.depth * k).toFixed(1)}" fill="#334155" stroke="#94a3b8"/>`;
  }
  for (const o of p.objects || []) {
    if (o.kind === "mark") continue;
    const col = { bar: "#f59e0b", dj_booth: "#a78bfa", speaker: "#475569", pillar: "#cbd5e1", riser: "#3f3f58", balcony: "#64748b", screen: "#38bdf8", door: "#22c55e" }[o.kind] || "#64748b";
    const cx = +X(o.x), cz = +Z(o.z);
    s += `<rect x="${(cx - o.w * k / 2).toFixed(1)}" y="${(cz - o.d * k / 2).toFixed(1)}" width="${Math.max(2, o.w * k).toFixed(1)}" height="${Math.max(2, o.d * k).toFixed(1)}" fill="${col}" fill-opacity="${o.kind === "riser" || o.kind === "balcony" ? 0.35 : 0.85}" transform="rotate(${-(o.rot || 0)} ${cx} ${cz})"><title>${esc(o.name || o.kind)}</title></rect>`;
  }
  for (const r of p.rigging || []) {
    s += `<line x1="${X(r.a[0])}" y1="${Z(r.a[2])}" x2="${X(r.b[0])}" y2="${Z(r.b[2])}" stroke="#e2e8f0" stroke-width="${r.kind === "truss" ? 4 : 2}" stroke-linecap="round" opacity=".8"><title>${esc(r.name)}</title></line>`;
  }
  // sizes along two walls, and which end is which
  s += `<text x="${w / 2}" y="${(+Z(z0) - 8).toFixed(1)}" text-anchor="middle" class="rp-dim">${(x1 - x0).toFixed(1)} m · stage end</text>`;
  s += `<text x="${(+X(x1) + 8).toFixed(1)}" y="${hgt / 2}" class="rp-dim" transform="rotate(90 ${(+X(x1) + 8).toFixed(1)} ${hgt / 2})" text-anchor="middle">${(z1 - z0).toFixed(1)} m</text>`;
  s += "</svg>";
  return s;
}

function numField(label, value, opts = {}) {
  const input = h("input", { type: "number", step: opts.step || 0.5, min: opts.min ?? 0.5, value: value ?? "", placeholder: opts.placeholder || "" });
  return [h("label.field", h("span", label), input), input];
}

export function openRoomDialog(start = "shape") {
  const hasRig = ((state.snap && state.snap.venue && state.snap.venue.rigging) || []).length > 0;
  const lights = ((state.snap && state.snap.patch) || []).length;
  const cur = (state.snap && state.snap.venue && state.snap.venue.room) || {};
  const tabs = h("div.seg.room-tabs");
  const pane = h("div.room-pane");
  let close = () => {};
  const done = (res) => { if (res && res.ok) close(); };

  // ---------------------------------------------------------------- shape
  const shapePane = () => {
    let shape = "rectangle";
    const [wF, w] = numField("Width m", cur.width || 16, { min: 3 });
    const [dF, d] = numField("Depth m", cur.depth || 20, { min: 3 });
    const [hF, ht] = numField("Ceiling m", cur.height || 5, { min: 2.2, step: 0.1 });
    const [cwF, cw] = numField("", "", { placeholder: "auto" });
    const [cdF, cd] = numField("", "", { placeholder: "auto" });
    const corner = h("select.select");
    const cornerF = h("label.field", h("span", ""), corner);
    const layout = h("input", { type: "checkbox" });
    layout.checked = !hasRig;
    const kind = h("select.select", ...KINDS.map(([k, l]) => h("option", { value: k }, l)));
    const kindF = h("label.field", h("span", "Kind of room"), kind);
    const preview = h("div.room-preview");
    const note = h("p.muted.small", "");
    const cards = h("div.shape-cards", ...SHAPES.map(([k, label]) => h("button.shape-card" + (k === shape ? ".on" : ""), {
      type: "button", dataset: { shape: k }, onclick: () => { shape = k; sync(); },
      html: `<svg viewBox="0 0 40 40"><polygon points="${THUMB[k]}"/></svg><span>${label}</span>`,
    })));
    const EXTRA = {
      l: [["Cut-out width m", "Cut-out depth m"], [["front-right", "Missing corner: far right"], ["front-left", "far left"], ["back-right", "stage end, right"], ["back-left", "stage end, left"]]],
      t: [["Narrow part width m", "Wide part depth m"], [["back", "Wide part at the stage end"], ["front", "Wide part at the far end"]]],
      u: [["Notch width m", "Notch depth m"], [["front", "Notch in the far wall"], ["back", "Notch in the stage wall"]]],
      octagon: [["Corner cut m", null], null],
      wedge: [["Narrow end width m", null], [["", "Narrow at the stage end"], ["back", "Narrow at the far end"]]],
    };
    const spec = () => ({
      shape, width: +w.value || 16, depth: +d.value || 20, height: +ht.value || 5,
      cut_w: cw.value ? +cw.value : null, cut_d: cd.value ? +cd.value : null, corner: corner.value || "",
      kind: kind.value, layout: layout.checked, bar: { side: "front" },
    });
    let timer = 0;
    const draw = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const r = await act("venue_preview", { spec: spec() });
        if (r.ok) preview.innerHTML = planSvg(r.preview);
        else preview.textContent = r.error || "";
      }, 120);
    };
    const sync = () => {
      cards.querySelectorAll(".shape-card").forEach((c) => c.classList.toggle("on", c.dataset.shape === shape));
      const ex = EXTRA[shape];
      cwF.hidden = !ex; cdF.hidden = !(ex && ex[0][1]); cornerF.hidden = !(ex && ex[1]);
      if (ex) {
        cwF.querySelector("span").textContent = ex[0][0];
        if (ex[0][1]) cdF.querySelector("span").textContent = ex[0][1];
        if (ex[1]) {
          const keep = corner.value;
          corner.replaceChildren(...ex[1].map(([v, l]) => h("option", { value: v }, l)));
          if ([...corner.options].some((o) => o.value === keep)) corner.value = keep;
          cornerF.querySelector("span").textContent = shape === "l" ? "Which corner" : "Which way";
        }
      }
      kindF.hidden = !layout.checked;
      note.textContent = layout.checked
        ? (lights ? `Replaces the rigging, objects and zones; your ${lights} light(s) stay patched (take them off their rigging).` : "Replaces the rigging, objects and zones with a starter layout that fits this shape.")
        : "Only the walls change: rigging and objects you have are moved inside, with the lights on them.";
      draw();
    };
    [w, d, ht, cw, cd].forEach((i) => i.addEventListener("input", draw));
    [corner, kind].forEach((i) => i.addEventListener("change", draw));
    layout.addEventListener("change", sync);
    const make = h("button.btn.primary", {
      onclick: async () => {
        const s = spec();
        done(await run("venue_shape", { ...s, keep_mounts: false }, { toast: true }));
      },
    }, "Make this room");
    const box = h("div",
      cards,
      h("div.room-cols",
        h("div",
          h("div.form-grid", wF, dF, hF),
          h("div.form-grid", cwF, cdF, cornerF),
          h("label.check", layout, h("span", "Starter layout: DJ / stage, dance floor, bar, trusses wall to wall")),
          h("div.form-grid", kindF),
          note, h("div.row-btns", make)),
        preview));
    sync();
    return box;
  };

  // ------------------------------------------------------------- describe
  const describePane = () => {
    const text = h("textarea.room-words", { rows: 3, placeholder: EXAMPLES[0] });
    const out = h("div.room-read");
    const preview = h("div.room-preview");
    const ai = !!(state.status && state.status.llm_configured);
    const read = async (apply) => {
      const words = text.value.trim() || text.placeholder;
      if (!text.value.trim()) text.value = words;
      out.replaceChildren(h("p.muted.small", ai ? "Asking the AI…" : "Reading…"));
      let res;
      try {
        const d = await post("/api/console/room", { text: words, apply: !!apply });
        res = d.result || d;
      } catch (e) {
        res = { ok: false, error: e.message };
      }
      if (!res.ok && res.error) { out.replaceChildren(h("p.out-bad", res.error)); return res; }
      out.replaceChildren(
        h("p.small", h("b", res.by === "ai" ? "The AI read: " : "Read: "), (res.understood || []).join(" · ") || "nothing I know yet"),
        (res.unsure || []).length ? h("p.muted.small", "Guessed: " + res.unsure.join(", ") + " - add them to the words to change them.") : null,
        res.note ? h("p.muted.small", res.note) : null);
      if (!apply && res.spec) {
        const p = await act("venue_preview", { spec: res.spec });
        if (p.ok) preview.innerHTML = planSvg(p.preview);
      }
      if (apply && res.ok) toast(res.summary || "Room made", "ok");
      return res;
    };
    return h("div",
      h("p.muted.small", "Say what the room is like: its size, shape, ceiling, and where things are - the DJ, the bar, a stage, pillars, a balcony, doors." + (ai ? " The AI reads it." : " Read offline (add an AI key for freer wording).")),
      text,
      h("div.chips", ...EXAMPLES.map((e) => h("button.chip", { type: "button", onclick: () => { text.value = e; read(false); } }, e.length > 44 ? e.slice(0, 42) + "…" : e))),
      h("div.row-btns", h("button.btn", { onclick: () => read(false) }, "Read it"),
        h("button.btn.primary", { onclick: async () => done(await read(true)) }, "Make this room")),
      h("div.room-cols", out, preview));
  };

  // ------------------------------------------------------------- template
  const templatePane = () => {
    const [wF, w] = numField("Width m", "", { placeholder: "its own" });
    const [dF, d] = numField("Depth m", "", { placeholder: "its own" });
    const [hF, ht] = numField("Ceiling m", "", { placeholder: "its own", step: 0.1 });
    return h("div",
      h("p.muted.small", "A ready-made room with its rigging, stage and zones. Sizes are optional."),
      h("div.form-grid", wF, dF, hF),
      h("div.tpl-cards", ...TEMPLATES.map(([k, label, size]) => h("button.tpl-card", {
        type: "button",
        onclick: async () => done(await run("venue_template", { name: k, width: +w.value || null, depth: +d.value || null, height: +ht.value || null }, { toast: true })),
      }, h("b", label), h("small", size)))));
  };

  // ----------------------------------------------------------- draw / plan
  const drawPane = () => h("div",
    h("p", "Click the corners of the room on the floor; click the first corner (or press Enter) to close it."),
    h("ul.muted.small",
      h("li", "Walls snap to right angles and a 10 cm grid; hold Shift for any angle."),
      h("li", "Type a length while drawing (e.g. 8.5 then Enter) to make the next wall exactly that long."),
      h("li", "Backspace takes the last corner back; Esc stops.")),
    h("div.row-btns", h("button.btn.primary", { onclick: () => { close(); import("./venuepanel.js").then((m) => m.startDrawRoom()); } }, "Draw the walls")));
  const planPane = () => h("div",
    h("p", "Upload a floor plan (image or PDF), set its scale by measuring one known wall, then trace the room on it."),
    h("div.row-btns", h("button.btn.primary", { onclick: () => { close(); import("./venuepanel.js").then((m) => m.startPlanUpload()); } }, "Upload a floor plan…")));

  const PANES = [["shape", "Shape & size", shapePane], ["describe", "Describe it", describePane], ["template", "Template", templatePane],
    ["draw", "Draw it", drawPane], ["plan", "Floor plan", planPane]];
  const show = (key) => {
    tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.k === key));
    pane.replaceChildren(PANES.find((p) => p[0] === key)[2]());
  };
  tabs.append(...PANES.map(([k, label]) => h("button", { type: "button", dataset: { k }, onclick: () => show(k) }, label)));
  close = modal({ title: "Make the room", body: h("div.room-dlg", tabs, pane), wide: true });
  show(start);
  return close;
}
