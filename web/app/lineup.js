// Line up lights with the real ones (Programmer -> Rig -> Line up...): the
// desk learns where each light's pan and tilt zero really are, so every
// aim, spot, move and the 3D agree with the real light.
//
// One step at a time, the desk doing the rest: the light comes on alone (to
// find it), the desk points it at a mark of the room by itself (the two
// marks furthest apart), you nudge the REAL light onto it with one arrow pad
// and press "It's on the mark" - twice - and the desk works it out and goes
// to the next light.  Invert / Swap, another spot and Forget are small links.
import { h, modal, toast } from "./ui.js";
import { run, select } from "./actions.js";
import { state, patch, selected, on } from "./store.js";

// a room plan's four sides, named the desk's way: the DJ / stage end at the
// top, the back of the room at the bottom, left and right as you look at the
// DJ from the dance floor (the audience's view - the 3D's Front view)
export function roomSides(x0, z0, W, D, M) {
  const f = Math.max(0.45, Math.max(W, D) * 0.03);
  const t = (x, y, s, rot = 0) => `<text x="${x}" y="${y}" class="side-txt" font-size="${f}"${rot ? ` transform="rotate(${rot} ${x} ${y})"` : ""}>${s}</text>`;
  return t(x0 + W / 2, z0 - M * 0.35, "DJ / stage end") + t(x0 + W / 2, z0 + D + M * 0.7, "back of the room")
    + t(x0 - M * 0.4, z0 + D / 2, "left", -90) + t(x0 + W + M * 0.4, z0 + D / 2, "right", 90);
}

const movers = (nums) => nums.map((n) => patch().find((x) => x.head_no === n))
  .filter((x) => x && (x.map || []).includes("pan") && (x.map || []).includes("tilt"));

// the room's marks (Arrange's marks, zones, the DJ...), and the two furthest
// apart: two marks spread over the floor make a sure line-up
const roomSpots = () => ((state.snap && state.snap.move_spots) || []).filter((s) => !String(s.key).startsWith("cell"));
// (never two spots at the same place - "the DJ" and "the DJ booth" made the
// second mark look like the first, and the box seemed not to move on)
function farPair(list) {
  const apart = list.filter((s, k) => list.findIndex((o) => Math.hypot(o.x - s.x, o.z - s.z) < 1.5) === k);
  list = apart;
  let best = list.slice(0, 2), far = -1;
  for (let a = 0; a < list.length; a++) {
    for (let b = a + 1; b < list.length; b++) {
      const d = Math.hypot(list[a].x - list[b].x, list[a].z - list[b].z);
      if (d > far) { far = d; best = [list[a], list[b]]; }
    }
  }
  return best;
}

export function openLineUp(heads = null) {
  let lights = movers(heads || selected());
  if (!lights.length) lights = movers(patch().map((x) => x.head_no));
  if (!lights.length) { toast("No moving lights to line up"); return; }
  const order = lights.map((x) => x.head_no);
  let i = 0;
  let step = 0;                                     // 0, 1: the marks; 2: lined up
  let marks = farPair(roomSpots());                 // the two marks, as the room has them
  let fine = false;
  let spread = 0;                                   // how far the two marks disagreed (degrees)
  const head = () => order[i];
  const now = () => patch().find((x) => x.head_no === head()) || {};
  const nameOf = (l) => l.name || `light ${l.head_no}`;
  const mark = () => marks[step] || null;

  const chips = h("div.lineup-chips");
  // where you are: Mark 1 (DJ) -> Mark 2 (Bar) -> Lined up, the one now lit
  const steps = h("div.lineup-steps");
  const said = h("p.small.lineup-said");             // what just happened (or went wrong), in the box itself
  const title = h("h3.lineup-title");
  const hint = h("p.muted.small.lineup-hint");
  const main = h("div.lineup-main");
  const more = h("div.lineup-more");

  // the arrow pad: the REAL light moves; Fine for the last bit
  // (this light - whatever else is selected: the arrows used to move the
  // selection, so with nothing or another light selected they did nothing here)
  const nudge = (axis, dir) => run("nudge", { axis, step: dir * (fine ? 0.002 : 0.02), heads: [head()] });
  const arrow = (label, axis, dir, cls) => h("button.btn.lineup-arrow" + cls, { "aria-label": label, title: label, onclick: () => nudge(axis, dir) },
    { up: "▲", down: "▼", left: "◀", right: "▶" }[cls.slice(1)]);
  const fineBtn = h("button.btn.small.lineup-fine", { onclick: () => { fine = !fine; fineBtn.classList.toggle("on", fine); fineBtn.textContent = fine ? "Fine ✓" : "Fine"; } }, "Fine");
  const pad = h("div.lineup-pad",
    arrow("Tilt one way", "tilt", 1, ".up"), arrow("Pan one way", "pan", -1, ".left"), fineBtn,
    arrow("Pan the other way", "pan", 1, ".right"), arrow("Tilt the other way", "tilt", -1, ".down"));

  // the light alone at full (to find it), aimed at the mark from where the
  // desk thinks it is - the arrows do the last bit
  const point = () => {
    const m = mark();
    if (m) run("aim_at", { x: m.x, y: 0, z: m.z, heads: [head()] }, { silentError: true });
  };
  const enter = async () => {
    await select([head()]);
    run("highlight", { state: true, solo: true }, { silentError: true });
    step = now().align ? 2 : 0;
    if (step < 2) point();
    paint();
  };
  // the desk picks this light's two marks: spots it can really reach, where it
  // plays (the dance floor, the DJ, your taped marks) - not the bar in
  // another room - as far apart as those go
  const pickMarks = async () => {
    const r = await run("line_up_marks", { head: head() }, { silentError: true, quiet: true });
    marks = r.ok && (r.marks || []).length ? r.marks.slice() : farPair(roomSpots());
  };
  const go = async (k) => { i = (k + order.length) % order.length; spread = 0; await pickMarks(); enter(); };

  const tell = (text, bad = false) => { said.textContent = text; said.classList.toggle("bad", bad); };
  let busy = false;
  const onMark = async (e) => {
    const m = mark();
    if (!m || busy) return;
    busy = true;
    if (e && e.currentTarget) { e.currentTarget.disabled = true; e.currentTarget.textContent = "Keeping it…"; }
    try {
      const r = await run("line_up", { head: head(), x: m.x, z: m.z }, { silentError: true });
      if (!r.ok) { tell(`That mark wasn't kept: ${r.error || "the desk didn't answer"}`, true); paint(); return; }
      if (step === 0) {
        step = 1; point();
        const next = mark();
        tell(next ? `✓ Mark 1 kept (${m.label}). Now the second one: ${next.label} - the light has moved there.`
          : `✓ Mark 1 kept (${m.label}). Now pick a second spot, away from the first.`);
        paint(); return;
      }
      const s = await run("line_up", { head: head(), solve: true }, { silentError: true });
      if (!s.ok) { tell(`It couldn't be worked out: ${s.error || "the desk didn't answer"}`, true); paint(); return; }
      spread = +s.spread || 0; step = 2; tell(""); paint();
    } finally { busy = false; }
  };

  // small links: which way it turns, another spot, forget
  const flips = () => h("div.lineup-row",
    ...[["invert_pan", "Invert pan"], ["invert_tilt", "Invert tilt"], ["swap", "Swap pan/tilt"]].map(([k, label]) =>
      h("button.btn.small" + ((now().orient || {})[k] ? ".on" : ""), {
        onclick: async () => { await run("set_orient", { heads: [head()], [k]: !((now().orient || {})[k]) }, { toast: true }); point(); paint(); },
      }, label)));
  // another spot: one of the room's marks, or TAP where it is on a plan of
  // the room (the desk works out the metres) - the metres can be typed too
  const otherSpot = () => {
    const sel = h("select.select", ...roomSpots().map((s, k) => h("option", { value: k }, s.label)),
      h("option", { value: "plan" }, "Somewhere else: tap it on the plan…"));
    const cur = roomSpots().findIndex((s) => mark() && s.x === mark().x && s.z === mark().z);
    if (cur >= 0) sel.value = String(cur);
    const use = (x, z) => { marks[step] = { label: "your spot", x: Math.round(x * 10) / 10, z: Math.round(z * 10) / 10 }; point(); paint(); };
    const v = (state.snap && state.snap.venue) || {}, room = v.room || {};
    const W = +room.width || 16, D = +room.depth || 20, x0 = (+room.cx || 0) - W / 2, z0 = +room.back || 0;
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    const M = Math.max(1.2, Math.max(W, D) * 0.07);   // room round the edges for their names
    svg.setAttribute("viewBox", `${x0 - M} ${z0 - M} ${W + 2 * M} ${D + 2 * M}`);
    svg.setAttribute("class", "lu-plan");
    svg.innerHTML = `<rect x="${x0}" y="${z0}" width="${W}" height="${D}" class="lu-room"/>`
      + (v.zones || []).map((zn) => `<polygon points="${zn.points.map((q) => q.join(",")).join(" ")}" class="lu-zone"><title>${zn.name || zn.kind}</title></polygon>`).join("")
      + (v.stage ? `<rect x="${v.stage.x - v.stage.width / 2}" y="${v.stage.z}" width="${v.stage.width}" height="${v.stage.depth}" class="lu-stage"/>` : "")
      + (v.zones || []).map((zn) => {
        const cx = zn.points.reduce((a, q) => a + q[0], 0) / zn.points.length, cz = zn.points.reduce((a, q) => a + q[1], 0) / zn.points.length;
        return `<text x="${cx}" y="${cz}" class="lu-txt">${String(zn.name || zn.kind).replace(/[<&>"]/g, "")}</text>`;
      }).join("")
      + roomSides(x0, z0, W, D, M);
    svg.addEventListener("click", (e) => {
      const pt = svg.createSVGPoint();
      pt.x = e.clientX; pt.y = e.clientY;
      const p = pt.matrixTransform(svg.getScreenCTM().inverse());
      use(p.x, p.y);
    });
    const x = h("input.num", { type: "number", step: 0.1, placeholder: "e.g. 2" });
    const z = h("input.num", { type: "number", step: 0.1, placeholder: "e.g. 6" });
    const planBox = h("div.lu-planbox",
      h("p.small", "Tap the plan where the spot is: the room seen from above, the DJ / stage end at the top, left and right as you look at the DJ from the dance floor."),
      svg,
      h("details.lineup-link", h("summary", "or type it in metres"),
        h("div.lineup-row", h("label.small", "Across ", x, " m (0 = the middle, minus = left, plus = right - as you look at the DJ from the dance floor)")),
        h("div.lineup-row", h("label.small", "From the back wall ", z, " m")),
        h("button.btn.small", { onclick: () => { if (x.value === "" || z.value === "") { toast("Type both: across and from the back wall"); return; } use(+x.value, +z.value); } }, "Use it")));
    planBox.hidden = roomSpots().length > 0;
    if (!roomSpots().length) sel.value = "plan";
    sel.addEventListener("change", () => {
      planBox.hidden = sel.value !== "plan";
      if (sel.value !== "plan") { marks[step] = roomSpots()[+sel.value]; point(); paint(); }
    });
    return h("div", h("div.lineup-row", sel), planBox);
  };
  const link = (label, body) => h("details.lineup-link", h("summary", label), body);

  const paint = () => {
    shownKey = key();
    const l = now();
    chips.replaceChildren(...order.map((n, k) => {
      const hd = patch().find((q) => q.head_no === n) || {};
      return h("button.chip" + (k === i ? ".on" : ""), { title: hd.align ? "Lined up" : "Not lined up yet", onclick: () => go(k) },
        (hd.align ? "✓ " : "") + nameOf(hd));
    }));
    const last = i === order.length - 1;
    // the two marks and the result, the one now in hand lit
    steps.replaceChildren(...[0, 1, 2].map((k) => h("span.lineup-step" + (k === step ? ".on" : k < step ? ".done" : ""),
      k < 2 ? `${k < step ? "✓" : k + 1} ${(marks[k] || {}).label || "a second spot"}` : `${step === 2 ? "✓" : "3"} Lined up`)));
    if (step === 2) {
      const a = l.align || { pan: 0, tilt: 0 };
      title.textContent = `✓ ${nameOf(l)} is lined up`;
      hint.textContent = spread > 3
        ? `The two marks disagree by ${spread.toFixed(1)}°: the light is probably placed a little off in the room (its height or position), or hangs the other way up. Check it in Arrange, then Do it again.`
        : `It was out by ${Math.abs(a.pan).toFixed(1)}° of pan and ${Math.abs(a.tilt).toFixed(1)}° of tilt - every look, spot and move now lands where the desk says.`;
      hint.classList.toggle("warn", spread > 3);
      main.replaceChildren(h("div.lineup-big",
        h("button.btn.primary.lineup-go", { onclick: () => (last ? close() : go(i + 1)) }, last ? "Done" : "Next light ▶"),
        h("button.btn.ghost", { onclick: async () => { step = 0; await pickMarks(); point(); paint(); } }, "Do it again")));
      more.replaceChildren(link("Forget this light's line-up", h("button.btn.small.danger", {
        onclick: async () => { await run("line_up", { head: head(), clear: true }, { toast: true }); step = 0; point(); paint(); } }, "Forget it")));
      return;
    }
    hint.classList.remove("warn");
    const m = mark();
    if (!m) {
      title.textContent = "The room has no marks yet";
      hint.textContent = "Tap where a spot on the floor is (a taped cross, the DJ) on the plan below - or add marks in Arrange.";
      main.replaceChildren(otherSpot());
      more.replaceChildren();
      return;
    }
    title.textContent = `Point the real ${nameOf(l)} at ${m.label}`;
    hint.textContent = `Mark ${step + 1} of 2. It's on now, alone, and aimed there - use the arrows until the REAL beam is on ${m.label}.`;
    main.replaceChildren(pad, h("div.lineup-big", h("button.btn.primary.lineup-go", { onclick: onMark }, `It's on ${m.label} ✓`)));
    if (step === 0) tell("Two spots, far apart: the desk knows where this light hangs and where they are in the room, so the "
      + "two tell it how far the real light is turned - pan and tilt - and check each other.");
    more.replaceChildren(
      link("The arrows move it the wrong way?", flips()),
      link("Use a different spot", otherSpot()),
      (l.align_marks || []).length ? h("button.btn.small.ghost", { onclick: async () => { await run("line_up", { head: head(), undo_mark: true }); step = Math.max(0, step - 1); point(); paint(); } }, "Back one mark") : null);
  };

  // a desk update repaints only when something this box shows changed (an
  // open link stays open while the light moves)
  let shownKey = "";
  const key = () => JSON.stringify([i, step, marks, order.map((n) => { const hd = patch().find((q) => q.head_no === n) || {}; return [hd.name, hd.align, hd.orient, (hd.align_marks || []).length]; })]);
  const paintOnce = () => { const k = key(); if (k !== shownKey) { shownKey = k; paint(); } };
  const off = on("snapshot", paintOnce);
  const close = modal({
    title: "Line up with the real lights",
    body: h("div.lineup", chips, steps, title, hint, said, main, more),
    foot: [h("span.muted.small.grow", "One light at a time: two marks each."),
      h("button.btn", { onclick: () => close() }, "Close")],
    onClose: () => { if (typeof off === "function") off(); run("highlight", { state: false }, { silentError: true }); },
  });
  go(0);
}
