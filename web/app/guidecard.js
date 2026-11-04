// How-to answers, laid out to read at a glance (the copilot's "how do I...?"):
//
//   [📖 GUIDE]  Lining up a venue
//   What it's for, in one line
//   Where:  Set up › Line up with the real lights…        <- the real buttons
//   ▓▓▓▓░░░░░░  2 of 5 done
//   ① Build the room roughly          [✓]
//      Settings › Venue … the text, with **names** as button chips
//   ② ...                              (4 shown, "Show all 5 steps")
//   See also: (Fixture library) (The room the lights know)
//
// A button name is a chip: tapping it lights up that button if it's on the
// screen now (or says it isn't).  Steps tick off as you do them, and the bar
// shows how far you are.  Nothing here changes the rig.
import { h, toast, modal } from "./ui.js";
import { get, post } from "./api.js";

const SHOWN = 4;

// the lighting words, in one line each (app/howto.py GLOSSARY): hovered in answers
let GLOSS = {};
let TOPICS = [];
const ready = get("/api/howto?topics=1").then((d) => { GLOSS = d.glossary || {}; TOPICS = d.topics || []; }).catch(() => {});
export const guideTopics = () => TOPICS;
export const glossaryWords = () => GLOSS;

// a word of the glossary in a piece of text: dotted, its meaning on hover
// (the first time in an answer only - every "cue" underlined is noise)
function withTerms(text, seen) {
  const terms = Object.keys(GLOSS).sort((a, b) => b.length - a.length);
  if (!terms.length || !seen) return [text];
  const re = new RegExp(`\\b(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(s|es)?\\b`, "i");
  const out = [];
  let rest = String(text);
  for (let guard = 0; guard < 40 && rest; guard++) {
    const m = rest.match(re);
    if (!m) break;
    const term = terms.find((t) => t.toLowerCase() === m[1].toLowerCase());
    if (seen.has(term)) {
      out.push(rest.slice(0, m.index + m[0].length));
    } else {
      seen.add(term);
      out.push(rest.slice(0, m.index), h("abbr.gc-term", { title: `${term}: ${GLOSS[term]}`, tabindex: 0 }, m[0]));
    }
    rest = rest.slice(m.index + m[0].length);
  }
  out.push(rest);
  return out;
}

// the text of a step: **names** as button chips, "A → B" as a breadcrumb,
// *words* in italics - built as elements, never as HTML from the text
export function richText(text, seen = null) {
  const out = [];
  const parts = String(text || "").split(/(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\s→\s)/g);
  for (const p of parts) {
    if (!p) continue;
    if (/^\*\*[^*]+\*\*$/.test(p)) out.push(uiName(p.slice(2, -2)));
    else if (/^\*[^*]+\*$/.test(p)) out.push(h("i", p.slice(1, -1)));
    else if (/^\s→\s$/.test(p)) out.push(h("span.gc-sep", { "aria-hidden": "true" }, "›"));
    else out.push(...withTerms(p, seen));
  }
  return out;
}

// a button / menu name, styled like one; a tap points at it on the screen
function uiName(name) {
  return h("button.gc-ui", { type: "button", title: `Show me “${name}” on the screen`, onclick: () => pointAt(name) }, name);
}

function norm(s) { return String(s || "").toLowerCase().replace(/[…\s]+$/g, "").replace(/\s+/g, " ").trim(); }

/** The control on the screen now with that name (not inside help itself). */
export function findOnScreen(name) {
  const want = norm(String(name).replace(/\s*\(.*?\)\s*/g, " "));
  if (!want) return null;
  const all = [...document.querySelectorAll("button, [role=tab], a, summary, label, .tb-btn, h3, h4")]
    .filter((el) => !el.closest(".guide-card, .coach, #cmd-results") && el.offsetParent !== null);
  return all.find((x) => norm(x.textContent) === want || norm(x.getAttribute("aria-label")) === want)
    || all.find((x) => norm(x.textContent).startsWith(want) || norm(x.title) === want) || null;
}

export function pointAt(name) {
  const el = findOnScreen(name);
  if (!el) { toast(`“${name}” isn't on the screen right now - the path above says where it is`, "", 4000); return; }
  el.scrollIntoView({ block: "nearest", inline: "nearest" });
  el.classList.remove("gc-pulse");
  void el.offsetWidth;                      // restart the pulse
  el.classList.add("gc-pulse");
  setTimeout(() => el.classList.remove("gc-pulse"), 2600);
}

// "Open it for me": the screen a topic is about, opened the way its own
// button does (and for a topic without one, its Where button, pressed)
const lazy = (mod, fn, ...args) => () => import(mod).then((m) => m[fn](...args));
const OPENERS = {
  "Lining up a venue": lazy("./lineup.js", "openLineUp"),
  "The room the lights know": lazy("./roomteach.js", "openRoomTeach"),
  "Arrange mode": lazy("./venuepanel.js", "setArranging", true),
  "Fixture library": lazy("./dialogs.js", "openAddDialog"),
  "Cues and playbacks": lazy("./cuedialogs.js", "openCueDialog"),
  "Report a bug": lazy("./bugreport.js", "openBugReport"),
  "DJ requests from a phone": lazy("./djreq.js", "openRequestsLink"),
  "What the AI has learned": lazy("./ailearning.js", "openLearning"),
  "Strobe safety": lazy("./settings.js", "openSettingsAt", "output"),
  "Connecting a rig": lazy("./settings.js", "openSettingsAt", "output"),
  "Hardware controllers": lazy("./settings.js", "openSettingsAt", "midi"),
  "Look and help": lazy("./settings.js", "openSettingsAt", "screen"),
  "A newer Haze": lazy("./settings.js", "openSettingsAt", "screen"),
  "The AI assistant": lazy("./copilot.js", "openCopilot"),
  "Talking with the copilot": lazy("./copilot.js", "openCopilot"),
  "Keyboard": lazy("./dialogs.js", "openHelp"),
  "Venue check-in": lazy("./checkin.js", "openCheckin"),
  "A light show per song": lazy("./songs.js", "openSongs"),
};
export function openerFor(card) {
  const key = Object.keys(OPENERS).find((k) => (card.topic || "").startsWith(k));
  if (key) return OPENERS[key];
  const last = (card.where || [])[card.where.length - 1];
  return last && findOnScreen(last) ? () => findOnScreen(last).click() : null;
}

/** The card for a guide answer.  `ask(topic)` asks about a "See also" topic;
 *  `question` is what was asked ("Not what I asked" keeps it for the guide). */
export function guideCard(card, also = [], ask = null, question = "") {
  const steps = card.steps || [];
  const seen = new Set();
  const done = new Set();
  const bar = h("div.gc-bar", { role: "progressbar", "aria-valuemin": 0, "aria-valuemax": steps.length, "aria-valuenow": 0 },
    h("i", { style: { width: "0%" } }));
  const count = h("span.gc-count", steps.length ? `${steps.length} step${steps.length > 1 ? "s" : ""}` : "");
  const paint = () => {
    bar.firstChild.style.width = `${steps.length ? (100 * done.size) / steps.length : 0}%`;
    bar.setAttribute("aria-valuenow", done.size);
    count.textContent = done.size ? (done.size === steps.length ? "All done ✓" : `${done.size} of ${steps.length} done`)
      : `${steps.length} step${steps.length > 1 ? "s" : ""}`;
  };
  const items = steps.map((s, i) => {
    const tick = h("input", { type: "checkbox", "aria-label": `Step ${i + 1} done`,
      onchange: () => { if (tick.checked) done.add(i); else done.delete(i); li.classList.toggle("done", tick.checked); paint(); } });
    const li = h("li.gc-step" + (i >= SHOWN ? ".more" : ""),
      h("span.gc-n", { "aria-hidden": "true" }, String(i + 1)),
      h("div.gc-body",
        s.title ? h("div.gc-title", ...richText(s.title, seen)) : null,
        s.text ? h("div.gc-text", ...richText(s.text, seen)) : null),
      h("label.gc-tick", { title: "Done" }, tick));
    return li;
  });
  const list = h("ol.gc-steps", ...items);
  const more = steps.length > SHOWN ? h("button.btn.small.ghost.gc-more", { type: "button", onclick: () => {
    list.classList.add("all"); more.remove();
  } }, `Show all ${steps.length} steps`) : null;
  const open = openerFor(card);
  const notThis = question ? h("button.btn.small.ghost.gc-notthis", { type: "button", title: "Keeps the question so the guide can be filled in",
    onclick: () => { post("/api/howto", { not_this: question }).catch(() => {}); notThis.replaceWith(h("span.muted.small", "Noted - thanks.")); } },
  "Not what I asked") : null;
  const actions = h("div.gc-actions",
    open ? h("button.btn.small.primary", { type: "button", onclick: () => { try { open(); } catch (e) { toast(e.message, "bad"); } } }, "Open it for me") : null,
    steps.length ? h("button.btn.small", { type: "button", onclick: () => startCoach(card) }, "▶ Show me step by step") : null,
    notThis);
  return h("div.guide-card",
    h("div.gc-head", h("span.gc-badge", "📖 Guide"), h("span.gc-topic", card.topic || "")),
    (card.unsure || []).length ? h("p.gc-unsure", `The guide doesn't mention “${card.unsure.join("”, “")}” - this is the nearest topic it has. `
      + "The question is kept so the guide can be filled in.") : null,
    card.summary ? h("p.gc-summary", ...richText(card.summary, seen)) : null,
    (card.where || []).length ? h("div.gc-where", h("span.gc-k", "Where"),
      ...card.where.flatMap((w, i) => [i ? h("span.gc-sep", { "aria-hidden": "true" }, "›") : null, uiName(w)]).filter(Boolean)) : null,
    steps.length > 1 ? h("div.gc-progress", bar, count) : null,
    steps.length ? list : null,
    more,
    (card.notes || []).length ? h("div.gc-notes", ...card.notes.map((n) => h("p", ...richText(n, seen)))) : null,
    actions,
    also.length ? h("div.gc-also", h("span.gc-k", "See also"),
      ...also.map((t) => h("button.chip", { type: "button", onclick: () => ask && ask(`how do I use ${t}`) }, t))) : null);
}

// An AI answer laid out the same way: numbered lines as steps, **names**
// as button chips, "A → B" as a breadcrumb; anything else as paragraphs
export function replyView(text) {
  const seen = new Set();
  const lines = String(text || "").split("\n");
  const out = [];
  let ol = null;
  let ul = null;
  for (const raw of lines) {
    const line = raw.trim();
    const num = line.match(/^(\d+)[.)]\s+(.*)/);
    const bullet = line.match(/^[-*•]\s+(.*)/);
    if (num) {
      if (!ol) { ol = h("ol.gc-steps.all.compact"); out.push(ol); }
      ul = null;
      ol.append(h("li.gc-step", h("span.gc-n", { "aria-hidden": "true" }, num[1]), h("div.gc-body", h("div.gc-text", ...richText(num[2], seen)))));
    } else if (bullet) {
      if (!ul) { ul = h("ul.gc-list"); out.push(ul); }
      ol = null;
      ul.append(h("li", ...richText(bullet[1], seen)));
    } else if (line) {
      ol = null; ul = null;
      out.push(h("p.gc-p", ...richText(line, seen)));
    }
  }
  return h("div.reply-view", ...out);
}


// -- the card in a window of its own (search, What's this?, a word) ---------
export async function openGuide(question) {
  await ready;
  const d = await get("/api/howto?q=" + encodeURIComponent(question)).catch(() => ({}));
  let body;
  if (d.glossary) {
    body = h("div.guide-card", h("div.gc-head", h("span.gc-badge", "📖 Word"), h("span.gc-topic", d.glossary.term)),
      h("p.gc-summary", d.glossary.text));
  } else if (d.found && d.card) {
    body = guideCard(d.card, d.also || [], (q) => { closeIt(); openGuide(q); }, question);
  } else {
    body = h("div.guide-card", h("div.gc-head", h("span.gc-badge", "📖 Guide"), h("span.gc-topic", "Not in the guide yet")),
      h("p.gc-summary", "That question is kept (on this computer) so the guide can be filled in. Topics it covers:"),
      h("div.gc-also", ...(d.also || []).slice(0, 10).map((t) => h("button.chip", { type: "button", onclick: () => { closeIt(); openGuide(`how do I use ${t}`); } }, t))));
  }
  const close = modal({ title: "Help", body, wide: true });
  function closeIt() { if (typeof close === "function") close(); }
  return close;
}

// -- "Show me step by step": the steps one at a time, on the real screen ----
// A small box says the step; the first button it names is lit up and stays
// lit; pressing that button (doing the step) moves on.  Next / Back / Stop.
let coach = null;
export function startCoach(card) {
  stopCoach();
  const steps = (card.steps || []).filter((s) => s.title || s.text);
  if (!steps.length) return;
  let i = 0;
  let lit = null;
  const box = h("div.coach", { role: "dialog", "aria-label": `${card.topic}: step by step` });
  const unlight = () => { if (lit) { lit.el.classList.remove("coach-lit"); lit.el.removeEventListener("click", lit.fn, true); lit = null; } };
  const paint = () => {
    unlight();
    const s = steps[i];
    const names = [...`${s.title} ${s.text}`.matchAll(/\*\*([^*]+)\*\*/g)].map((m) => m[1]);
    const where = i === 0 ? (card.where || []) : [];
    const target = [...names, ...where].map((n) => findOnScreen(n)).find(Boolean);
    const seen = new Set();
    box.replaceChildren(
      h("div.coach-head", h("span.gc-badge", `Step ${i + 1} of ${steps.length}`), h("span.coach-topic", card.topic),
        h("button.btn.small.ghost.coach-x", { type: "button", "aria-label": "Stop", onclick: stopCoach }, "✕")),
      h("div.gc-bar", h("i", { style: { width: `${(100 * i) / steps.length}%` } })),
      s.title ? h("div.gc-title", ...richText(s.title, seen)) : null,
      s.text ? h("div.gc-text", ...richText(s.text, seen)) : null,
      h("div.coach-hint.muted.small", target ? "The lit button is the next thing to press - pressing it moves on." : "Do this step, then Next."),
      h("div.coach-btns",
        h("button.btn.small", { type: "button", disabled: i === 0, onclick: () => { i--; paint(); } }, "Back"),
        i < steps.length - 1
          ? h("button.btn.small.primary", { type: "button", onclick: () => { i++; paint(); } }, "Next")
          : h("button.btn.small.primary", { type: "button", onclick: () => { stopCoach(); toast("All done ✓", "ok"); } }, "Done")));
    if (target) {
      target.classList.add("coach-lit");
      target.scrollIntoView({ block: "nearest", inline: "nearest" });
      const fn = () => setTimeout(() => { if (coach === box && i < steps.length - 1) { i++; paint(); } }, 350);
      target.addEventListener("click", fn, true);
      lit = { el: target, fn };
    }
  };
  box._unlight = unlight;
  document.body.append(box);
  coach = box;
  paint();
}
export function stopCoach() {
  if (!coach) return;
  coach._unlight();
  coach.remove();
  coach = null;
}
// Esc stops the coach - and only the coach (the copilot it came from stays open)
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && coach) { e.preventDefault(); e.stopImmediatePropagation(); stopCoach(); }
}, true);
