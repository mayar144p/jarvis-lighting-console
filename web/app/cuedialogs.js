// Cue dialogs: record / update a cue, its fade, hold, follow and part times,
// cue actions, and a playback's cue list.

import { state, on } from "./store.js";
import { run } from "./actions.js";
import { h, modal, promptBox, menu } from "./ui.js";

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
  ["osc_send", "Send OSC", "address", "/go  or  /cue/5/start 1"],
];

function openCueActions(n, c, done) {
  const list = JSON.parse(JSON.stringify(c.actions || []));
  const box = h("div");
  const draw = () => box.replaceChildren(...(list.length ? list.map((a, i) => {
    const def = CUE_ACTS.find((d) => d[0] === a.action) || [a.action, a.action, "", ""];
    const osc = a.action === "osc_send";
    const shown = osc ? [a.args.address, a.args.value].filter((x) => x !== undefined && x !== "").join(" ") : a.args[def[2]];
    const inp = def[2] ? h("input", { type: "text", value: shown ?? "", placeholder: def[3] }) : null;
    if (inp) inp.addEventListener("change", () => {
      const v = inp.value.trim();
      if (osc) {
        // "/address value": the address, then one value (a number when it is one)
        const [addr, ...rest] = v.split(/\s+/);
        const val = rest.join(" ");
        a.args = { address: addr || "", ...(val !== "" ? { value: !isNaN(+val) ? +val : val } : {}) };
        return;
      }
      a.args[def[2]] = v !== "" && !isNaN(+v) ? +v : v;
    });
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
          // everything else about the cue in one menu (drag the ⋮⋮ to move it)
          h("button.btn.small.ghost.icon", { title: "Update, move, insert, delete…", "aria-label": `Cue ${c.n} options`, onclick: (e) => menu(e.currentTarget, [
            { label: "Merge the programmer into it", hint: "adds the changes, keeps the rest", run: () => run("record_cue", { playback: n, cue: c.n, mode: "merge" }, { toast: true }) },
            { label: "Replace it with the programmer", hint: "exactly what the programmer holds", run: () => run("record_cue", { playback: n, cue: c.n, mode: "replace" }, { toast: true }) },
            { label: "Record a new cue before it", run: () => run("record_cue", { playback: n, cue: c.n, mode: "insert" }, { toast: true }) },
            { label: "Merge, cue only", hint: "tracking: the change stops at this cue", disabled: !pb.tracking, run: () => run("record_cue", { playback: n, cue: c.n, mode: "merge", cue_only: true }, { toast: true }) },
            "-",
            { label: "Move up", disabled: i === 0, run: () => run("move_cue", { playback: n, cue: c.n, to: c.n - 1 }).then(refresh) },
            { label: "Move down", disabled: i === stack.length - 1, run: () => run("move_cue", { playback: n, cue: c.n, to: c.n + 1 }).then(refresh) },
            { label: "Insert an empty cue below", run: () => run("insert_cue", { playback: n, at: c.n + 1 }).then(refresh) },
            "-",
            { label: "Preview edit…", hint: "edit it in 3D only - the rig doesn't see", run: () => run("blind", { playback: n, cue: c.n }, { toast: true }) },
            { label: (c.block ? "✓ " : "") + "Block", hint: "tracking: start afresh here", disabled: !pb.tracking, run: () => run("cue_set", { playback: n, cue: c.n, block: !c.block }, { toast: true }).then(refresh) },
            { label: `Actions… ${(c.actions || []).length ? "(" + c.actions.length + ")" : ""}`, hint: "buttons, macros, other lists, timeline, tempo", run: () => openCueActions(n, c, refresh) },
            "-",
            { label: "Delete", danger: true, hint: "Ctrl+Z brings it back", run: () => run("delete_cue", { playback: n, cue: c.n }).then(refresh) },
          ]) }, "⋯"))));
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
