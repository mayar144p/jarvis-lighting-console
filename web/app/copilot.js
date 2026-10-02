// The copilot drawer: plain English -> a plan you can read -> apply as one
// undoable edit.  Also: design a show from a brief, and diagnose the rig.
import { get, post, act } from "./api.js";
import { state, on } from "./store.js";
import { run } from "./actions.js";
import { $, $$, h, toast, confirmBox } from "./ui.js";

const history = [];            // [{role, content}] for follow-ups ("warmer")

function label(step) {
  const a = { ...(step.attributes || {}), ...(step.fx || {}), ...(step.timing || {}) };
  const tgt = step.target && step.target !== "auto" ? ` → ${step.target}` : "";
  switch (step.action) {
    case "set_intensity": return `Intensity ${a.level}%${a.fade ? ` over ${a.fade}s` : ""}${tgt}`;
    case "set_colour": return `Colour ${a.hex || a.colour}${tgt}`;
    case "set_position": return `Aim pan ${a.pan ?? "–"} tilt ${a.tilt ?? "–"}${tgt}`;
    case "set_attribute": return `${a.attribute} ${a.value}${tgt}`;
    case "run_fx": return `Effect ${a.name || `${a.kind || "sine"} ${a.attribute || ""}`}${a.speed ? ` @ ${a.speed} Hz` : ""}${tgt}`;
    case "blackout": return a.state ? "Blackout" : "Release blackout";
    case "add_heads": return `Add ${a.qty || 1} × ${a.query || "fixture"}`;
    case "record_cue": return `Record cue${a.name ? ` “${a.name}”` : ""} on PB${a.playback || 1}`;
    case "cue_go": return `GO${a.playback ? ` PB${a.playback}` : ""}`;
    case "quick_defaults": return `Make ${a.count || "a page of"} ${a.focus ? a.focus + " " : ""}buttons${a.page ? ` on page ${a.page}` : ""}`;
    case "timeline_build": return `${a.bars || 16}-bar build-up on the timeline${a.start != null ? ` at ${a.start} s` : ` before the ${a.before || "drop"}`}`;
    case "chase_colours": return `Chase ${(a.colours || []).join(" / ")}${a.beats ? `, ${a.beats} beat(s) a step` : ""}${a.section ? ` during the ${a.section}` : " now"}${a.rig ? ` → ${a.rig}` : tgt}`;
    default: return step.action.replace(/_/g, " ") + tgt;
  }
}

function say(kind, text, extra) {
  const log = $("#ai-log");
  const box = h("div.msg." + kind, text);
  if (extra) box.append(extra);
  log.append(box);
  log.scrollTop = log.scrollHeight;
  return box;
}

// Steps the 3D-only preview can't hold back: these reach the rig at once.
const LIVE_ALWAYS = new Set(["cue_go", "cue_back", "cue_forward", "playback_level", "playback_release",
  "playback_activate", "blackout", "master", "timeline_play", "timeline_seek", "timeline_from_playback"]);

function planView(plan, onApply) {
  const steps = plan.steps || [];
  if (!steps.length) return null;
  const list = h("div.plan", ...steps.map((s, i) => h("div.plan-step", h("span.n", i + 1), label(s))));
  const apply = h("button.btn.primary.small", "Apply");
  const preview = h("button.btn.small.plan-preview", {
    title: "Run it in the 3D view only - the real lights keep what they have until you press Keep",
  }, "Preview in 3D");
  const discard = h("button.btn.small", "Discard");
  const bar = h("div.plan-actions", apply, preview, discard);
  const mark = (res) => {
    const runs = (res && res.steps_run) || [];
    [...list.children].forEach((row, i) => row.classList.add(runs[i] ? (runs[i].ok ? "ok" : "bad") : "bad"));
  };
  apply.addEventListener("click", async () => {
    apply.disabled = preview.disabled = discard.disabled = true;
    const res = await onApply();
    mark(res);
    bar.replaceChildren(h("span.muted.small", res && res.ok ? "Applied - Ctrl+Z undoes all of it." : `Nothing changed: ${(res && res.error) || "failed"}`));
  });
  preview.addEventListener("click", async () => {
    if (state.snap && state.snap.blind && state.snap.blind.on) { toast("Already in preview - Apply runs it in 3D only"); return; }
    apply.disabled = preview.disabled = discard.disabled = true;
    const on = await act("blind", { state: true });
    if (!on.ok) { toast(on.error || "Can't preview", "bad"); apply.disabled = preview.disabled = discard.disabled = false; return; }
    const res = await onApply();
    mark(res);
    if (!(res && res.ok)) {
      await act("blind", { state: false });
      bar.replaceChildren(h("span.muted.small", `Nothing changed: ${(res && res.error) || "failed"}`));
      return;
    }
    const live = steps.filter((s) => LIVE_ALWAYS.has(s.action)).length;
    const keep = h("button.btn.primary.small", "Keep");
    const drop = h("button.btn.small", "Throw away");
    const done = (text) => bar.replaceChildren(h("span.muted.small", text));
    keep.addEventListener("click", async () => {
      keep.disabled = drop.disabled = true;
      await act("blind", { state: false, keep: true });
      done("Kept - the rig has it now. Ctrl+Z undoes all of it.");
    });
    drop.addEventListener("click", async () => {
      keep.disabled = drop.disabled = true;
      await act("undo");
      await act("blind", { state: false });
      done("Thrown away - the rig never saw it.");
    });
    bar.replaceChildren(h("span.small.plan-previewing", "Previewing in 3D only - the real lights haven't changed."
      + (live ? ` (${live} step(s) - cues, blackout, master, timeline - went live anyway.)` : "")), keep, drop);
  });
  discard.addEventListener("click", () => bar.replaceChildren(h("span.muted.small", "Discarded.")));
  return h("div", list, bar);
}

// ---------------------------------------------------------- the assistant
// With an AI key: a loop of tools on the desk (it looks, acts, checks the
// real lights, asks, remembers).  Without one: the plan-first copilot below.
let session = "";
try { session = sessionStorage.getItem("jarvis.ai.session") || ""; } catch (e) { /* ignore */ }
if (!session) {
  session = Math.random().toString(36).slice(2) + Date.now().toString(36);
  try { sessionStorage.setItem("jarvis.ai.session", session); } catch (e) { /* ignore */ }
}
let attached = null;           // a photo to match (data URL), for the next message

const assistantOn = () => !!(state.status && state.status.llm_configured) && !$("#ai-offline").checked;

// a small JPEG: the 3D view, or a photo, shrunk to at most `side` pixels
function shrink(url, side = 768) {
  return new Promise((ok) => {
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, side / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(img.width * k)); c.height = Math.max(1, Math.round(img.height * k));
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      ok(c.toDataURL("image/jpeg", 0.72));
    };
    img.onerror = () => ok(null);
    img.src = url;
  });
}

async function viewShot() {
  try {
    const m = await import("./stagepanel.js");
    const st = m.getStage && m.getStage();
    return st ? await shrink(st.photo(1024)) : null;
  } catch (e) { return null; }
}

function setAttached(url) {
  attached = url;
  const el = $("#ai-att");
  el.hidden = !url;
  el.replaceChildren(...(url ? [h("img", { src: url, alt: "" }), h("button.x", { title: "Remove the photo", onclick: () => setAttached(null) }, "×")] : []));
}

async function assist(text) {
  const btn = $("#ai-send");
  btn.disabled = true;
  btn.textContent = "…";
  const t0 = performance.now();
  const thinking = say("bot", "Working…");
  const tick = setInterval(() => { thinking.firstChild.textContent = `Working… ${Math.round((performance.now() - t0) / 1000)} s`; }, 1000);
  try {
    const image = attached || ($("#ai-see").checked ? await viewShot() : null);
    setAttached(null);
    let r = await post("/api/console/assistant", { message: text, session, image, preview: true, can_see: !!(window.jarvisStage && !window.jarvisStage.failed) });
    // the AI asked to see its changes: let the 3D draw them, then send a picture
    while (r.ok && r.need_view) {
      thinking.firstChild.textContent = "Looking at the 3D…";
      await new Promise((ok) => setTimeout(ok, 900));
      const shot = await viewShot().catch(() => null);
      r = await post("/api/console/assistant", { resume: r.turn, image: shot });
    }
    clearInterval(tick);
    thinking.remove();
    if (r.no_key) { $("#ai-offline").checked = true; say("bot", r.error); plan(text); return; }
    if (!r.ok) { say("bot", "The AI couldn't do that: " + (r.error || "no answer")); return; }
    const extra = h("div");
    if (r.steps && r.steps.length) {
      extra.append(h("details.ai-steps", h("summary", `What I did (${r.steps.length})`),
        ...r.steps.map((s) => h("div.plan-step" + (s.ok ? ".ok" : ".bad"), h("span.n", s.ok ? "✓" : "✕"), s.summary || s.action))));
    }
    if (r.question) {
      extra.append(h("div.ai-q", h("b", r.question),
        h("div.chip-row", ...(r.options || []).map((o) => h("button.chip", { onclick: () => { say("user", o); assist(o); } }, o)))));
    }
    if (r.preview) {
      const keep = h("button.btn.primary.small", "Keep");
      const drop = h("button.btn.small", "Throw away");
      const bar = h("div.plan-actions", h("span.small.plan-previewing", "Previewing in 3D only - the real lights haven't changed."), keep, drop);
      const done = (t) => bar.replaceChildren(h("span.muted.small", t));
      keep.addEventListener("click", async () => { keep.disabled = drop.disabled = true; await act("blind", { state: false, keep: true }); done("Kept - the rig has it now. Ctrl+Z undoes all of it."); });
      drop.addEventListener("click", async () => { keep.disabled = drop.disabled = true; await act("undo"); await act("blind", { state: false }); done("Thrown away - the rig never saw it."); });
      extra.append(bar);
    } else if (r.changed) {
      extra.append(h("div.muted.small", "Done - Ctrl+Z undoes all of it."));
    }
    const box = say("bot", r.reply || (r.question ? "" : "Done."), extra);
    box.firstChild.after(h("span.src", "AI"));
  } catch (err) {
    clearInterval(tick);
    thinking.remove();
    say("bot", "Could not reach the AI: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Send";
  }
}

async function showMemory() {
  const r = await post("/api/console/assistant", { notes: true });
  const box = h("div");
  // forgetting renumbers the notes, so the list is redrawn from the answer
  const draw = (list) => box.replaceChildren(...list.map((n, i) => h("div.ai-note", h("span", `${i + 1}. ${n}`),
    h("button.x", { title: "Forget this", "aria-label": "Forget this", onclick: async () => {
      const r2 = await post("/api/console/assistant", { notes: true, forget: i });
      draw(r2.notes || []);
    } }, "×"))));
  const list = r.notes || [];
  draw(list);
  say("bot", list.length ? "What I remember about how you like things:" : "I haven't noted anything yet - tell me how you like things and I'll remember.",
    list.length ? box : null);
}

async function plan(text) {
  const offline = $("#ai-offline").checked;
  const btn = $("#ai-send");
  btn.disabled = true;
  btn.textContent = "…";
  const thinking = say("bot", "Planning…");
  try {
    const d = await post("/api/console/ai", { message: text, offline, history: history.slice(-8) });
    const r = d.result || {};
    thinking.remove();
    history.push({ role: "user", content: text }, { role: "assistant", content: r.reply || "" });
    const src = h("span.src", r.source === "llm" ? "AI" : "OFFLINE");
    const view = planView(r, async () => {
      const a = await post("/api/console/ai", { steps: r.steps, reply: r.reply, apply: true });
      return (a.result && a.result.run) || { ok: false, error: "no result" };
    });
    const box = say("bot", r.reply || "Here is the plan.", view);
    box.firstChild.after(src);
    if (r.note) box.append(h("div.muted.small", r.note));
    if (r.answer) box.append(h("div", { style: { marginTop: "6px" } }, r.answer));
  } catch (err) {
    thinking.remove();
    say("bot", "Could not plan that: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Plan";
  }
}

export function askCopilot(text) {
  openCopilot("chat");
  say("user", text);
  plan(text);
}

function suggestions() {
  const p = (state.snap && state.snap.patch) || [];
  const movers = p.some((x) => x.body && x.body.moving);
  const colour = p.some((x) => (x.map || []).includes("red"));
  const out = [];
  if (!p.length) return ["Add 6 LED pars", "Add 4 moving head spots"];
  if (colour) out.push("Warm amber wash, everything at 70%", "Slow rainbow across the rig");
  if (movers) out.push("Movers circle slowly", "Point the movers at centre stage");
  out.push("Everything to 50% over 3 seconds", "Blackout");
  return out.slice(0, 5);
}

function renderSuggest() {
  $("#ai-suggest").replaceChildren(...suggestions().map((s) => h("button.chip", { onclick: () => { say("user", s); plan(s); } }, s)));
}

// ---------------------------------------------------------- show design
async function design() {
  const text = $("#design-text").value.trim();
  if (!text) { toast("Describe the show first"); return; }
  const box = $("#concepts");
  box.replaceChildren(h("p.muted", "Designing…"));
  try {
    const d = await post("/api/console/generate", { prompt: text, offline: $("#ai-offline").checked });
    const r = d.result || {};
    const concepts = (r.design && r.design.concepts) || [];
    box.replaceChildren(...concepts.map((c) => h("div.concept",
      h("h4", c.name || "Concept"),
      h("div.muted.small", c.tagline || c.summary || ""),
      h("div.sw", ...(c.palette || []).map((p) => h("i", { title: p.name || p.hex, style: { background: p.hex || p } }))),
      h("ol", ...(c.cues || []).slice(0, 6).map((q) => h("li", `${q.name || "cue"}${q.fade_s !== undefined ? ` · ${q.fade_s}s` : ""}`))),
      h("button.btn.primary.small", {
        onclick: async () => {
          if (!(await confirmBox("Load concept", `Load “${c.name}” as a cue list on PB1? It replaces the cues on PB1. Nothing plays until you press GO.`, { ok: "Load" }))) return;
          const res = await post("/api/console/import_show", { concept: c, playback: 1, name: c.name }).catch((e) => ({ error: e.message }));
          const rr = res.result || {};
          if (res.error || !rr.ok) toast(res.error || rr.error || "failed", "bad");
          else toast(rr.summary || "Loaded on PB1 - press GO", "ok");
        },
      }, "Load on PB1"))));
    if (!concepts.length) box.replaceChildren(h("p.muted", "No concepts came back - try a fuller description."));
    if (r.design && r.design.assumptions && r.design.assumptions.length) {
      box.prepend(h("p.muted.small", r.design.assumptions.join(" · ")));
    }
  } catch (err) {
    box.replaceChildren(h("p.muted", err.message));
  }
}

// ------------------------------------------------------------ whole show
const STYLES = ["Techno warehouse", "House party", "EDM festival drop", "Hip hop club", "Chill lounge", "Wedding dance", "Latin night"];
let autoDesign = null;

function sectionBar(sections, length) {
  const total = length || (sections.length ? sections[sections.length - 1].end : 1) || 1;
  return h("div.auto-bar", ...sections.map((s) => {
    const seg = h("i", { title: `${s.name} · ${s.start.toFixed(0)}-${s.end.toFixed(0)} s` }, s.name);
    seg.style.flexGrow = String(Math.max(0.5, s.end - s.start));
    seg.style.background = `hsl(${220 - s.energy * 220}, 70%, ${28 + s.energy * 22}%)`;
    return seg;
  }));
}

async function autoshow() {
  const text = $("#auto-text").value.trim();
  const box = $("#auto-plan");
  box.replaceChildren(h("p.muted", "Reading the rig and designing…"));
  let r;
  try {
    const d = await post("/api/console/autoshow", { prompt: text, offline: $("#ai-offline").checked });
    r = d.result || {};
  } catch (err) { box.replaceChildren(h("p.muted", err.message)); return; }
  if (r.error || !r.design) { box.replaceChildren(h("p.muted", r.error || "No design came back")); return; }
  autoDesign = r.design;
  const groups = (r.analysis && r.analysis.groups) || {};
  const bpm = (r.analysis && r.analysis.bpm) || 120;
  const spb = 240 / bpm;
  let t = 0;
  const rough = autoDesign.sections.map((s) => { const start = t; t += s.bars * spb; return { name: s.name, start, end: t, energy: s.energy }; });
  box.replaceChildren(...[
    h("div.auto-head", h("b", autoDesign.name), h("span.muted.small", r.source === "llm" ? "designed by the AI" : "designed offline"),
      h("div.sw", ...(autoDesign.palette || []).map((c) => h("i", { style: { background: c }, title: c })))),
    r.note ? h("p.muted.small", r.note) : null,
    sectionBar(rough),
    h("ol.auto-sections", ...autoDesign.sections.map((s) => h("li",
      h("b", s.name), h("span.muted.small", ` ${s.bars} bars · energy ${Math.round(s.energy * 100)}%`),
      h("ul", ...s.looks.map((lk) => h("li",
        lk.colour ? h("i.dot", { style: { background: lk.colour } }) : h("i.dot.none"),
        `${(groups[lk.group] || {}).name || lk.group}: ${lk.intensity}%`
        + (lk.aim ? ` → ${lk.aim.replace("_", " ")}` : "") + (lk.fx ? ` · ${lk.fx.replace("_", " ")}` : "")
        + (lk.gobo ? " · gobo" : "") + (lk.zoom ? ` · ${lk.zoom}` : ""))),
        ...s.hits.map((hit) => h("li.hit", `${hit.kind} ${(groups[hit.group] || {}).name || hit.group} every ${hit.every}`
          + (hit.last_bars ? ` in the last ${hit.last_bars} bars` : ""))))))),
    h("div.row-btns",
      h("button.btn.primary", { onclick: async () => {
        const pb = +$("#auto-pb").value || 1;
        if (!(await confirmBox("Build the show", `Build “${autoDesign.name}”? It replaces the cues on PB${pb} and the timeline's Auto tracks (your own tracks stay). Ctrl+Z undoes all of it.`, { ok: "Build" }))) return;
        const res = await post("/api/console/autoshow", { apply: true, design: autoDesign, playback: pb }).catch((e) => ({ error: e.message }));
        const rr = res.result || {};
        if (res.error || rr.error || !rr.built) { toast(res.error || rr.error || "failed", "bad"); return; }
        toast(rr.summary || "Show built", "ok", 6000);
        const tlBtn = document.querySelector('#pb-mode button[data-mode="timeline"]');
        if (tlBtn) tlBtn.click();
      } }, "Build it on the timeline"),
      h("button.btn", { onclick: autoshow }, "Another take")),
  ].filter(Boolean));
}

// -------------------------------------------------------------- doctor
async function diagnose() {
  const box = $("#doctor");
  box.replaceChildren(h("p.muted", "Checking…"));
  try {
    const d = await get("/api/console/doctor");
    const found = d.findings || [];
    box.replaceChildren(...found.map((f) => h("div.finding." + (f.level || "warn"),
      h("b", f.title), h("p", f.detail || ""),
      f.fix ? h("button.btn.small", { style: { marginTop: "8px" }, onclick: () => run(f.fix.action, f.fix.params || {}, { toast: true }).then(diagnose) }, f.fix.label || "Fix it") : null)));
    if (d.summary) box.prepend(h("p.muted.small", d.summary));
  } catch (err) {
    box.replaceChildren(h("p.muted", err.message));
  }
}

export function openCopilot(tab = "chat") {
  $("#copilot").classList.remove("hidden");
  $("#ai-btn").classList.add("on");
  $$("#copilot-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.ctab === tab)));
  $$("#copilot .ctab").forEach((p) => { p.hidden = p.dataset.cpane !== tab; });
  renderSuggest();
  if (tab === "chat") setTimeout(() => $("#ai-text").focus(), 20);
}

export function closeCopilot() {
  $("#copilot").classList.add("hidden");
  $("#ai-btn").classList.remove("on");
}

export const copilotOpen = () => !$("#copilot").classList.contains("hidden");

export function initCopilot() {
  $("#ai-btn").addEventListener("click", () => (copilotOpen() ? closeCopilot() : openCopilot()));
  $("#copilot-close").addEventListener("click", closeCopilot);
  $$("#copilot-tabs button").forEach((b) => b.addEventListener("click", () => openCopilot(b.dataset.ctab)));
  $("#ai-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#ai-text").value.trim();
    if (!text) return;
    $("#ai-text").value = "";
    say("user", text);
    if (assistantOn()) assist(text); else plan(text);
  });
  $("#ai-attach").addEventListener("click", () => $("#ai-photo").click());
  $("#ai-photo").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (f) setAttached(await shrink(URL.createObjectURL(f)));
  });
  $("#ai-text").addEventListener("paste", async (e) => {
    const f = [...(e.clipboardData ? e.clipboardData.files : [])].find((x) => x.type.startsWith("image/"));
    if (f) { e.preventDefault(); setAttached(await shrink(URL.createObjectURL(f))); }
  });
  $("#ai-text").addEventListener("drop", async (e) => {
    const f = [...(e.dataTransfer ? e.dataTransfer.files : [])].find((x) => x.type.startsWith("image/"));
    if (f) { e.preventDefault(); setAttached(await shrink(URL.createObjectURL(f))); }
  });
  $("#ai-new").addEventListener("click", async () => {
    await post("/api/console/assistant", { reset: true, session });
    $("#ai-log").replaceChildren();
    say("bot", "New conversation. What would you like?");
  });
  $("#ai-memory").addEventListener("click", showMemory);
  $("#ai-operator").addEventListener("click", () => import("./aioperator.js").then((m) => m.openOperator()));
  const syncTools = () => { $("#ai-tools").hidden = !assistantOn(); $("#ai-send").textContent = assistantOn() ? "Send" : "Plan"; };
  $("#ai-offline").addEventListener("change", syncTools);
  $("#ai-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ai-form").requestSubmit(); }
  });
  $("#design-btn").addEventListener("click", design);
  $("#auto-btn").addEventListener("click", autoshow);
  $("#auto-styles").replaceChildren(...STYLES.map((st) => h("button.chip", { onclick: () => { $("#auto-text").value = st; autoshow(); } }, st)));
  $("#doctor-btn").addEventListener("click", diagnose);
  on("snapshot", () => { if (copilotOpen()) renderSuggest(); });
  const st = state.status;
  $("#ai-status").textContent = st && st.llm_configured ? st.model : "offline compiler";
  if (!(st && st.llm_configured)) $("#ai-offline").checked = true;
  syncTools();
  say("bot", assistantOn()
    ? "Tell me what you want - a look, a feeling (\"the drop needs to hit harder\"), or a question about the show. I'll work the desk, check the real lights, and show it in 3D first; one Ctrl+Z undoes it."
    : "Tell me the look you want in plain words. I'll show you the plan first - nothing touches the rig until you press Apply, and one Ctrl+Z undoes it.");
}
