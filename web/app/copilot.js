// The copilot drawer: plain English -> a plan you can read -> apply as one
// undoable edit.  Also: design a show from a brief, and diagnose the rig.
import { get, post, act } from "./api.js";
import { state, on } from "./store.js";
import { run } from "./actions.js";
import { $, $$, h, toast, confirmBox, menu } from "./ui.js";
import { aiModes, setAiMode } from "./aisettings.js";
import { stagePanel } from "./view3d.js";
import { currentPage, lastButton } from "./quickbuttons.js";

// What the operator is looking at, for "this button", "this page", "here":
// the screen, the tab, the buttons page, the last button touched, open windows
function screenContext() {
  const tab = document.querySelector("#prog-tabs button[aria-selected='true']");
  const names = (state.snap && state.snap.quick && state.snap.quick.names) || {};
  const pg = currentPage();
  const wins = [...document.querySelectorAll(".modal[aria-label]")].map((m) => m.getAttribute("aria-label")).filter(Boolean).slice(0, 3);
  const out = { screen: { setup: "Set up", live: "Live" }[document.body.dataset.mode] || "Program" };
  if (tab) out.programmer_tab = tab.textContent.trim();
  out.buttons_page = pg + (names[pg] || names[String(pg)] ? ` (${names[pg] || names[String(pg)]})` : "");
  const b = lastButton();
  if (b) out.last_button = b;
  if (wins.length) out.open_windows = wins;
  return out;
}

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

// The guide's words as text: its lines kept, **names** in bold (no HTML from
// the text itself)
function guideText(text) {
  return h("div.guide-answer", ...String(text).split("\n").map((line) => h("div",
    ...line.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? h("b", part) : part.replace(/\*([^*]+)\*/g, "$1"))))));
}

function say(kind, text, extra) {
  const log = $("#ai-log");
  const box = h("div.msg." + kind, text);
  if (extra) box.append(extra);
  log.append(box);
  log.scrollTop = log.scrollHeight;
  if (kind === "user") {
    talked = true;                              // (the conversation has started: the suggestions go)
    const sug = $("#ai-suggest");
    if (sug) sug.replaceChildren();
  }
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
try { session = sessionStorage.getItem("haze.ai.session") || ""; } catch (e) { /* ignore */ }
if (!session) {
  session = Math.random().toString(36).slice(2) + Date.now().toString(36);
  try { sessionStorage.setItem("haze.ai.session", session); } catch (e) { /* ignore */ }
}
let attached = null;           // a photo to match (data URL), for the next message

function showAi(st) {
  const ai = (st && st.ai) || {};
  const mode = { online: "Online", local: "Local", auto: "Auto" }[ai.mode] || "";
  $("#ai-status").textContent = st && st.llm_configured ? `${st.model}${mode ? " · " + mode : ""} ▾` : "offline compiler ▾";
}

// no AI set up (or the key was refused): the desk's own offline planner
// answers; the AI is chosen in the list above (Online / Local / Auto)
let noKey = false;
const assistantOn = () => !!(state.status && state.status.llm_configured) && !noKey;

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
    const m = await stagePanel();
    const st = m && m.getStage();
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
  // live: what it's doing now, the steps done so far, and Stop
  const live = h("div.ai-live");
  const stopBtn = h("button.btn.small.danger", { type: "button", title: "Stop: it ends at its next step; what it did so far stays in the preview",
    onclick: async () => { stopBtn.disabled = true; stopBtn.textContent = "Stopping…"; await post("/api/console/assistant", { stop: true, session }).catch(() => {}); } }, "■ Stop");
  thinking.append(live, stopBtn);
  let polling = false;
  const tick = setInterval(async () => {
    const s = Math.round((performance.now() - t0) / 1000);
    if (polling) return;
    polling = true;
    const p = await post("/api/console/assistant", { progress: true, session }).catch(() => null);
    polling = false;
    if (!thinking.isConnected) return;
    thinking.firstChild.textContent = `${(p && p.running && p.now) || "Working"}… ${s} s`;
    if (p && p.steps) live.replaceChildren(...p.steps.slice(-6).map((x) => h("div.plan-step" + (x.ok ? ".ok" : ".bad"), h("span.n", x.ok ? "✓" : "✕"), x.summary || "")));
  }, 800);
  try {
    const image = attached || ($("#ai-see").checked ? await viewShot() : null);
    setAttached(null);
    let r = await post("/api/console/assistant", { message: text, session, image, preview: true, can_see: !!(window.hazeStage && !window.hazeStage.failed), screen: screenContext() });
    // the AI asked to see its changes: let the 3D draw them, then send a picture
    while (r.ok && r.need_view) {
      thinking.firstChild.textContent = "Looking at the 3D…";
      await new Promise((ok) => setTimeout(ok, 900));
      const shot = await viewShot().catch(() => null);
      r = await post("/api/console/assistant", { resume: r.turn, image: shot });
    }
    clearInterval(tick);
    thinking.remove();
    if (r.no_key) { noKey = true; say("bot", r.error); plan(text); return; }
    if (r.ai_note) toast(r.ai_note, "", 6000);          // "Gemini limit reached - using the offline AI"
    if (!r.ok) { say("bot", "The AI couldn't do that: " + (r.error || "no answer")); return; }
    if (r.stopped) toast("Stopped", "", 3000);
    // notes it would like to remember: only kept when you say so
    const asks = (r.learned || []).filter((n) => n.status === "pending");
    const extra = h("div");
    for (const n of asks) {
      const box = h("div.ai-q", h("b", `Remember this? “${n.note}”`), n.replaces ? h("div.muted.small", `Replaces: “${n.replaces}”`) : null);
      const answer = (keep) => post("/api/console/assistant", { learning: true, op: keep ? "keep" : "drop", id: n.id })
        .then(() => box.replaceChildren(h("span.muted.small", keep ? "Remembered." : "Not remembered.")));
      box.append(h("div.chip-row", h("button.chip", { onclick: () => answer(true) }, "Keep"), h("button.chip", { onclick: () => answer(false) }, "No")));
      extra.append(box);
    }
    if (r.steps && r.steps.length) {
      extra.append(h("details.ai-steps", h("summary", `What I did (${r.steps.length})`),
        ...r.steps.map((s) => h("div.plan-step" + (s.ok ? ".ok" : ".bad"), h("span.n", s.ok ? "✓" : "✕"), s.summary || s.action))));
      // what it looked up online, and where it read it
      const web = r.steps.filter((s) => s.ok && (s.action === "search_web" || s.action === "read_web"));
      const links = [...new Set(web.flatMap((s) => s.sources || []))].slice(0, 6);
      if (web.length) {
        extra.append(h("div.ai-web.muted.small", "🔎 " + web.filter((s) => s.action === "search_web").length + " look-up(s) online",
          ...links.map((u) => { let host = u; try { host = new URL(u).hostname.replace(/^www\./, ""); } catch (e) { /* as is */ }
            return h("a", { href: u, target: "_blank", rel: "noopener noreferrer", title: u, style: { marginLeft: "8px" } }, host); })));
      }
    }
    if (r.plan) {
      // the plan for a big job, before anything is changed
      extra.append(h("div.ai-plan",
        h("b", r.plan.summary || "The plan"),
        (r.plan.colours || []).length ? h("div.sw", ...r.plan.colours.map((c) => h("i", { title: c, style: { background: c } }))) : null,
        h("ol", ...(r.plan.steps || []).map((s) => h("li", s)))));
    }
    if (r.question) {
      extra.append(h("div.ai-q", h("b", r.question),
        h("div.chip-row", ...(r.options || []).map((o) => h("button.chip", { onclick: () => {
          if (r.plan && o === "Change something") { $("#ai-text").focus(); $("#ai-text").placeholder = "What should be different?"; return; }
          say("user", o); assist(r.plan && o === "Go ahead" ? "Go ahead with the plan, exactly as shown." : o);
        } }, o)))));
    }
    // what the AI prepared but only the operator may do: going live, ARM,
    // firing effects, saving / opening, deleting - one tap each
    for (const c of r.confirm || []) {
      const yes = h("button.btn.primary.small", "Do it");
      const no = h("button.btn.small", "No");
      const row = h("div.plan-actions.ai-confirm", h("span.small", c.label), yes, no);
      const answer = async (ok) => {
        yes.disabled = no.disabled = true;
        const res = await post("/api/console/assistant", { confirm: c.id, yes: ok }).catch((e) => ({ error: e.message }));
        row.replaceChildren(h("span.muted.small", res.error ? `Not done: ${res.error}` : (res.summary || (ok ? "Done" : "Not done"))));
      };
      yes.addEventListener("click", () => answer(true));
      no.addEventListener("click", () => answer(false));
      extra.append(row);
    }
    if (r.preview) {
      const keep = h("button.btn.primary.small", "Keep");
      const drop = h("button.btn.small", "Throw away");
      const bar = h("div.plan-actions", h("span.small.plan-previewing", "Previewing in 3D only - the real lights haven't changed."), keep, drop);
      const done = (t) => bar.replaceChildren(h("span.muted.small", t));
      // (kept = a good example for teaching the offline AI, thrown away = a bad one)
      const verdict = (good) => r.example && post("/api/console/assistant", { example: r.example, good }).catch(() => {});
      keep.addEventListener("click", async () => { keep.disabled = drop.disabled = true; await act("blind", { state: false, keep: true }); verdict(true); done("Kept - the rig has it now. Ctrl+Z undoes all of it."); });
      drop.addEventListener("click", async () => { keep.disabled = drop.disabled = true; await act("undo"); await act("blind", { state: false }); verdict(false); done("Thrown away - the rig never saw it."); });
      extra.append(bar);
    } else if (r.example && !r.changed) {
      // an answer: was it right? (teaches the offline AI)
      const rate = (good) => (e) => { post("/api/console/assistant", { example: r.example, good }).catch(() => {});
        e.currentTarget.parentElement.replaceChildren(h("span.muted.small", good ? "Thanks - noted as a good answer." : "Noted - it'll learn from it.")); };
      extra.append(h("div.ai-rate", h("button.btn.small.ghost", { title: "A good answer", onclick: rate(true) }, "👍"),
        h("button.btn.small.ghost", { title: "Not right", onclick: rate(false) }, "👎")));
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
    clearInterval(tick);
    thinking.remove();
    btn.disabled = false;
    btn.textContent = "Send";
  }
}

// what the AI has learnt, and your say over it (keep / forget / pause)
function showMemory() {
  import("./ailearning.js").then((m) => m.openLearning());
}

async function plan(text) {
  const offline = !assistantOn();
  const btn = $("#ai-send");
  btn.disabled = true;
  btn.textContent = "…";
  const thinking = say("bot", "Planning…");
  try {
    const d = await post("/api/console/ai", { message: text, offline, history: history.slice(-8) });
    const r = d.result || {};
    thinking.remove();
    history.push({ role: "user", content: text }, { role: "assistant", content: r.reply || "" });
    if (r.source === "guide") {
      // a how-to question: the guide's own steps, readable - nothing to apply
      const box = say("bot", "", guideText(r.reply || ""));
      box.prepend(h("span.src", "GUIDE"));
      return;
    }
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

// (the example chips are gone - the box says what to type)
// What it can do, and what to ask next - for this desk as it is now (no
// lights yet: add some; no room: draw it; no show: design one...).  Shown
// until the conversation starts; a tap puts the words in the box.
// (the welcome line isn't the conversation: the suggestions stay until
// something is asked - they used to vanish before they were ever seen)
let talked = false;
function renderSuggest() {
  const box = $("#ai-suggest");
  if (!box) return;
  if (talked) { box.replaceChildren(); return; }
  const s = state.snap || {};
  const lights = (s.patch || []).length;
  const zones = ((s.venue || {}).zones || []).length;
  const shows = (s.playbacks || []).some((p) => (p.stack || []).length) || ((s.quick || {}).buttons || []).length > 12;
  const asks = [];
  if (!lights) asks.push("Add 4 moving heads and 8 LED PARs on the truss", "Here's a photo of my rig - set it up");
  if (lights && !zones) asks.push("Draw the dance floor, the VIP, the bar and the DJ booth");
  if (lights && !shows) asks.push("Design me a Halloween show", "Only buttons for tonight, no cues or timeline");
  if (shows) asks.push("Run the show to the music", "Christmas with the same programming as this show", "Check the show before doors");
  if (lights) asks.push("Warm wash on the VIP, slow movement on the dance floor", "The drop needs to hit harder", "Calm and warm for the speeches");
  asks.push("What can you do?");
  const pick = (t) => { const ta = $("#ai-text"); ta.value = t; ta.focus(); };
  box.replaceChildren(
    h("div.ai-can.muted.small", "I can run the whole desk: design shows and buttons, set up the rig and the room, aim, colour and move lights, "
      + "look things up online, check the show before doors - and I ask when something's unclear. Big jobs: I show the plan first."),
    ...asks.slice(0, 6).map((t) => h("button.chip", { type: "button", onclick: () => pick(t) }, t)));
}

// ---------------------------------------------------------- show design
async function design() {
  const text = $("#design-text").value.trim();
  if (!text) { toast("Describe the show first"); return; }
  const box = $("#concepts");
  box.replaceChildren(h("p.muted", "Designing…"));
  try {
    const d = await post("/api/console/generate", { prompt: text, offline: !assistantOn() });
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
let autoDesign = null;

// "Put its cues on": each playback (a GO fader on the Live page), and
// whether it's empty or what would be replaced
function fillPlaybacks() {
  const sel = $("#auto-pb");
  if (!sel) return;
  const pbs = (state.snap && state.snap.playbacks) || [];
  const keep = sel.value;
  const list = pbs.length ? pbs : Array.from({ length: 10 }, (_, i) => ({ n: i + 1 }));
  sel.replaceChildren(...list.map((p) => {
    const n = (p.stack || p.cues || []).length || p.count || 0;
    const name = p.name && !/^(PB|Playback) ?\d+$/i.test(p.name) ? ` "${p.name}"` : "";
    return h("option", { value: String(p.n) }, `Fader ${p.n}${name} - ${n ? `${n} cue${n > 1 ? "s" : ""}, replaced` : "empty"}`);
  }));
  const firstEmpty = list.find((p) => !((p.stack || p.cues || []).length || p.count));
  sel.value = keep && list.some((p) => String(p.n) === keep) ? keep : String((firstEmpty || list[0]).n);
}

function sectionBar(sections) {
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
    const d = await post("/api/console/autoshow", { prompt: text, variants: 3 });
    r = d.result || {};
  } catch (err) { box.replaceChildren(h("p.muted", err.message)); return; }
  if ((r.designs || []).length > 1) showTakes(r); else showPlan(r);
}

// Three takes on the brief: each a card (its colours, its parts by energy);
// open one to see it whole and build it
function showTakes(r) {
  const box = $("#auto-plan");
  const bpm = (r.analysis && r.analysis.bpm) || 120;
  box.replaceChildren(h("p.muted.small", "Three takes on it - open one to see it whole, then build it."),
    h("div.auto-takes", ...r.designs.map((d, i) => {
      let t = 0;
      const rough = d.sections.map((s) => { const start = t; t += s.bars * 240 / bpm; return { name: s.name, start, end: t, energy: s.energy }; });
      return h("div.auto-take",
        h("div.auto-take-head", h("b", String.fromCharCode(65 + i)), h("span", d.take || d.name)),
        h("div.sw", ...(d.palette || []).map((c) => h("i", { style: { background: c }, title: c }))),
        sectionBar(rough),
        h("span.muted.small", `${d.sections.length} parts · ${d.sections.reduce((n, s) => n + s.hits.length, 0)} hits`),
        h("button.btn.small.primary", { onclick: () => showPlan({ ...r, design: d, designs: null }) }, "Open this one"));
    })));
}

/** Change part of the show on the timeline, as asked: previewed like a new one. */
async function changeShow() {
  const ask = $("#auto-ask").value.trim();
  if (!ask) { $("#auto-ask").focus(); return; }
  const box = $("#auto-plan");
  box.replaceChildren(h("p.muted", `Changing: “${ask}”…`));
  let r;
  try {
    const d = await post("/api/console/autoshow", { refine: ask, offline: !assistantOn() });
    r = d.result || {};
  } catch (err) { box.replaceChildren(h("p.muted", err.message)); return; }
  showPlan(r, ask);
}

function showPlan(r, ask = "") {
  const box = $("#auto-plan");
  if (r.error || !r.design) { box.replaceChildren(h("p.muted", r.error || "No design came back")); return; }
  autoDesign = r.design;
  const changed = new Set(r.changed || []);
  const groups = (r.analysis && r.analysis.groups) || {};
  const bpm = (r.analysis && r.analysis.bpm) || 120;
  const spb = 240 / bpm;
  let t = 0;
  const rough = autoDesign.sections.map((s) => { const start = t; t += s.bars * spb; return { name: s.name, start, end: t, energy: s.energy }; });
  const by = r.source === "llm" ? "the AI" : "the desk's own designer";
  box.replaceChildren(...[
    h("div.auto-head", h("b", autoDesign.name), h("span.muted.small", ask ? `changed by ${by}` : `designed by ${by}`),
      h("div.sw", ...(autoDesign.palette || []).map((c) => h("i", { style: { background: c }, title: c })))),
    r.note ? h("p.muted.small", r.note) : null,
    ask ? h("p.small", `“${ask}”: ${r.summary || ""} - the rest stays as it is.`) : null,
    sectionBar(rough),
    h("ol.auto-sections", ...autoDesign.sections.map((s) => h("li" + (changed.has(s.name) ? ".changed" : ""),
      h("b", s.name), h("span.muted.small", ` ${s.bars} bars · energy ${Math.round(s.energy * 100)}%`),
      changed.has(s.name) ? h("span.auto-changed", "changed") : null,
      h("ul", ...s.looks.map((lk) => h("li",
        lk.colour ? h("i.dot", { style: { background: lk.colour } }) : h("i.dot.none"),
        `${(groups[lk.group] || {}).name || lk.group}: ${lk.intensity}%`
        + (lk.aim ? ` → ${lk.aim.replace("_", " ")}` : "") + (lk.fx ? ` · ${lk.fx.replace("_", " ")}` : "")
        + (lk.gobo ? " · gobo" : "") + (lk.zoom ? ` · ${lk.zoom}` : ""))),
        ...s.hits.map((hit) => h("li.hit", `${hit.kind} ${(groups[hit.group] || {}).name || hit.group} every ${hit.every}`
          + (hit.last_bars ? ` in the last ${hit.last_bars} bars` : ""))))))),
    h("div.row-btns",
      h("button.btn.primary", { onclick: async () => {
        const pb = ask ? (autoDesign.playback || +$("#auto-pb").value || 1) : (+$("#auto-pb").value || 1);
        if (!(await confirmBox(ask ? "Build the change" : "Build the show", `Build “${autoDesign.name}”? It replaces the cues on PB${pb} and the timeline's Auto tracks (your own tracks stay). Ctrl+Z undoes all of it.`, { ok: "Build" }))) return;
        // a change to the show rebuilds its cues and timeline; its buttons stay
        // (it used to make a new button profile - "Halloween 2" - every time)
        const res = await post("/api/console/autoshow", { apply: true, design: autoDesign, playback: pb, buttons: !ask }).catch((e) => ({ error: e.message }));
        const rr = res.result || {};
        if (res.error || rr.error || !rr.built) { toast(res.error || rr.error || "failed", "bad"); return; }
        toast(rr.summary || "Show built", "ok", 6000);
        // earlier builds made "Auto · ..." copies of groups; this one doesn't
        const copies = rr.built.auto_groups || 0;
        if (copies) box.append(h("div.row-btns", h("button.btn", { title: "The show selects its lights directly now; your own groups stay", onclick: (e) => {
          e.currentTarget.disabled = true;
          run("group_delete", { auto: true }, { toast: true });
        } }, `Tidy away the ${copies} “Auto ·” group copies earlier builds made`)));
        // a song on the timeline: keep the new show in it too
        const sg = (state.snap && state.snap.songs) || {};
        const song = (sg.songs || []).find((x) => x.id === sg.current);
        if (song) box.append(h("div.row-btns", h("button.btn", { onclick: (e) => {
          e.currentTarget.disabled = true;
          run("song_save", { id: song.id }, { toast: true });
        } }, `Keep it in the song “${song.name}”`)));
        const tlBtn = document.querySelector('#pb-mode button[data-mode="timeline"]');
        if (tlBtn) tlBtn.click();
      } }, ask ? "Build the change" : "Build it on the timeline"),
      ask ? null : h("button.btn", { onclick: autoshow }, "Another take")),
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

// its left edge sizes it (kept on this computer); double-click: back to normal
function wireCopilotWidth() {
  const box = $("#copilot");
  const keep = (w) => { try { if (w) localStorage.setItem("haze.copilotW", String(w)); else localStorage.removeItem("haze.copilotW"); } catch (e) { /* private window */ } };
  const set = (w) => { if (w) box.style.setProperty("--copilot-w", `${w}px`); else box.style.removeProperty("--copilot-w"); };
  try { set(+localStorage.getItem("haze.copilotW") || 0); } catch (e) { /* private window */ }
  const grip = h("div.copilot-grip", { title: "Drag to make the Copilot wider or narrower (double-click: back to normal)" });
  box.append(grip);
  let x0 = 0, w0 = 0, on = false;
  grip.addEventListener("pointerdown", (e) => { on = true; x0 = e.clientX; w0 = box.getBoundingClientRect().width; grip.setPointerCapture(e.pointerId); grip.classList.add("on"); });
  grip.addEventListener("pointermove", (e) => { if (on) set(Math.round(Math.max(360, Math.min(window.innerWidth * 0.8, w0 + (x0 - e.clientX))))); });
  const end = () => { if (!on) return; on = false; grip.classList.remove("on"); keep(Math.round(box.getBoundingClientRect().width)); };
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
  grip.addEventListener("dblclick", () => { set(0); keep(0); });
}

export function initCopilot() {
  wireCopilotWidth();
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
  $("#ai-requests").addEventListener("click", () => import("./djreq.js").then((m) => m.openRequestsLink()));
  // talk with it, live (online AI): it hears, answers out loud, and has the
  // desk do what's asked; what was said goes in the conversation
  let stopTalk = null;
  const talkBtn = $("#ai-talk");
  if (talkBtn) talkBtn.addEventListener("click", async () => {
    if (stopTalk) { stopTalk(); stopTalk = null; talkBtn.classList.remove("on"); talkBtn.textContent = "🗣 Talk"; return; }
    if (!assistantOn()) { toast("Talking needs the online AI (Settings -> AI)", "bad"); return; }
    talkBtn.classList.add("on"); talkBtn.textContent = "■ Stop talking";
    const status = say("bot", "Connecting…");
    try {
      const m = await import("./voicelive.js");
      stopTalk = await m.startLive({
        state: (t) => { if (t) status.firstChild.textContent = t; },
        heard: (t) => say("user", t),
        said: (t) => say("bot", t),
        did: (t) => { if (t) say("bot", "✓ " + t); },
        error: (t) => { say("bot", "The talk stopped: " + t); talkBtn.classList.remove("on"); talkBtn.textContent = "🗣 Talk"; stopTalk = null; },
      });
    } catch (e) {
      status.firstChild.textContent = "Couldn't start talking: " + e.message + " - hold 🎙 to speak one request instead.";
      talkBtn.classList.remove("on"); talkBtn.textContent = "🗣 Talk"; stopTalk = null;
    }
  });
  // hold to talk: the words go in as if typed, and are sent
  import("./voice.js").then((m) => m.wireMic($("#ai-mic"), (text) => {
    say("user", text);
    if (assistantOn()) assist(text); else plan(text);
  }));
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
    talked = false;
    renderSuggest();
  });
  $("#ai-memory").addEventListener("click", showMemory);
  $("#ai-operator").addEventListener("click", () => import("./aioperator.js").then((m) => m.openOperator()));
  const syncTools = () => { $("#ai-tools").hidden = !assistantOn(); $("#ai-send").textContent = assistantOn() ? "Send" : "Plan"; };
  $("#ai-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ai-form").requestSubmit(); }
  });
  $("#design-btn").addEventListener("click", design);
  $("#auto-btn").addEventListener("click", autoshow);
  $("#auto-ask-form").addEventListener("submit", (e) => { e.preventDefault(); changeShow(); });
  $("#auto-styles").replaceChildren();
  $("#doctor-btn").addEventListener("click", diagnose);
  on("snapshot", () => { if (copilotOpen()) renderSuggest(); fillPlaybacks(); });
  fillPlaybacks();
  const st = state.status;
  showAi(st);
  // the AI switch: Online / Local / Auto, and the AI's settings
  $("#ai-status").addEventListener("click", (e) => {
    const cur = (state.status && state.status.ai && state.status.ai.mode) || "";
    menu(e.currentTarget, [
      ...aiModes.map(([k, label, hint]) => ({ label: (k === cur ? "✓ " : "") + label, hint, run: async () => {
        const d = await setAiMode(k);
        if (d && state.status) {
          state.status.ai = d;
          state.status.llm_configured = d.available;
          if (d.available) noKey = false;
          showAi(state.status);
          syncTools();
        }
      } })),
      "-",
      { label: "AI settings…", run: () => import("./settings.js").then((m) => m.openSettingsAt("ai")) },
    ]);
  });
  syncTools();
  say("bot", assistantOn()
    ? "Tell me what you want - a look, a feeling (\"the drop needs to hit harder\"), or a question about the show. I'll work the desk, check the real lights, and show it in 3D first; one Ctrl+Z undoes it."
    : "Tell me the look you want in plain words. I'll show you the plan first - nothing touches the rig until you press Apply, and one Ctrl+Z undoes it.");
}
