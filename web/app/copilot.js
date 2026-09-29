// The copilot drawer: plain English -> a plan you can read -> apply as one
// undoable edit.  Also: design a show from a brief, and diagnose the rig.
import { get, post } from "./api.js";
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

function planView(plan, onApply) {
  const steps = plan.steps || [];
  if (!steps.length) return null;
  const list = h("div.plan", ...steps.map((s, i) => h("div.plan-step", h("span.n", i + 1), label(s))));
  const apply = h("button.btn.primary.small", "Apply");
  const discard = h("button.btn.small", "Discard");
  const bar = h("div.plan-actions", apply, discard);
  apply.addEventListener("click", async () => {
    apply.disabled = discard.disabled = true;
    const res = await onApply();
    const runs = (res && res.steps_run) || [];
    [...list.children].forEach((row, i) => row.classList.add(runs[i] ? (runs[i].ok ? "ok" : "bad") : "bad"));
    bar.replaceChildren(h("span.muted.small", res && res.ok ? "Applied - Ctrl+Z undoes all of it." : `Nothing changed: ${(res && res.error) || "failed"}`));
  });
  discard.addEventListener("click", () => bar.replaceChildren(h("span.muted.small", "Discarded.")));
  return h("div", list, bar);
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
    plan(text);
  });
  $("#ai-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ai-form").requestSubmit(); }
  });
  $("#design-btn").addEventListener("click", design);
  $("#doctor-btn").addEventListener("click", diagnose);
  on("snapshot", () => { if (copilotOpen()) renderSuggest(); });
  const st = state.status;
  $("#ai-status").textContent = st && st.llm_configured ? st.model : "offline compiler";
  if (!(st && st.llm_configured)) $("#ai-offline").checked = true;
  say("bot", "Tell me the look you want in plain words. I'll show you the plan first - nothing touches the rig until you press Apply, and one Ctrl+Z undoes it.");
}
