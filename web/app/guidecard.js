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
import { h, toast } from "./ui.js";

const SHOWN = 4;

// the text of a step: **names** as button chips, "A → B" as a breadcrumb,
// *words* in italics - built as elements, never as HTML from the text
export function richText(text) {
  const out = [];
  const parts = String(text || "").split(/(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\s→\s)/g);
  for (const p of parts) {
    if (!p) continue;
    if (/^\*\*[^*]+\*\*$/.test(p)) out.push(uiName(p.slice(2, -2)));
    else if (/^\*[^*]+\*$/.test(p)) out.push(h("i", p.slice(1, -1)));
    else if (/^\s→\s$/.test(p)) out.push(h("span.gc-sep", { "aria-hidden": "true" }, "›"));
    else out.push(p);
  }
  return out;
}

// a button / menu name, styled like one; a tap points at it on the screen
function uiName(name) {
  return h("button.gc-ui", { type: "button", title: `Show me “${name}” on the screen`, onclick: () => pointAt(name) }, name);
}

function norm(s) { return String(s || "").toLowerCase().replace(/[…\s]+$/g, "").replace(/\s+/g, " ").trim(); }

export function pointAt(name) {
  const want = norm(name.replace(/\s*\(.*?\)\s*/g, " "));
  if (!want) return;
  const all = [...document.querySelectorAll("button, [role=tab], a, summary, label, .tb-btn, h3, h4")]
    .filter((el) => !el.closest(".guide-card") && el.offsetParent !== null);
  const el = all.find((x) => norm(x.textContent) === want || norm(x.getAttribute("aria-label")) === want)
    || all.find((x) => norm(x.textContent).startsWith(want) || norm(x.title) === want);
  if (!el) { toast(`“${name}” isn't on the screen right now - the path above says where it is`, "", 4000); return; }
  el.scrollIntoView({ block: "nearest", inline: "nearest" });
  el.classList.remove("gc-pulse");
  void el.offsetWidth;                      // restart the pulse
  el.classList.add("gc-pulse");
  setTimeout(() => el.classList.remove("gc-pulse"), 2600);
}

/** The card for a guide answer.  `ask(topic)` asks about a "See also" topic. */
export function guideCard(card, also = [], ask = null) {
  const steps = card.steps || [];
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
        s.title ? h("div.gc-title", ...richText(s.title)) : null,
        s.text ? h("div.gc-text", ...richText(s.text)) : null),
      h("label.gc-tick", { title: "Done" }, tick));
    return li;
  });
  const list = h("ol.gc-steps", ...items);
  const more = steps.length > SHOWN ? h("button.btn.small.ghost.gc-more", { type: "button", onclick: () => {
    list.classList.add("all"); more.remove();
  } }, `Show all ${steps.length} steps`) : null;
  return h("div.guide-card",
    h("div.gc-head", h("span.gc-badge", "📖 Guide"), h("span.gc-topic", card.topic || "")),
    card.summary ? h("p.gc-summary", ...richText(card.summary)) : null,
    (card.where || []).length ? h("div.gc-where", h("span.gc-k", "Where"),
      ...card.where.flatMap((w, i) => [i ? h("span.gc-sep", { "aria-hidden": "true" }, "›") : null, uiName(w)]).filter(Boolean)) : null,
    steps.length > 1 ? h("div.gc-progress", bar, count) : null,
    steps.length ? list : null,
    more,
    (card.notes || []).length ? h("div.gc-notes", ...card.notes.map((n) => h("p", ...richText(n)))) : null,
    also.length ? h("div.gc-also", h("span.gc-k", "See also"),
      ...also.map((t) => h("button.chip", { type: "button", onclick: () => ask && ask(`how do I use ${t}`) }, t))) : null);
}

// An AI answer laid out the same way: numbered lines as steps, **names**
// as button chips, "A → B" as a breadcrumb; anything else as paragraphs
export function replyView(text) {
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
      ol.append(h("li.gc-step", h("span.gc-n", { "aria-hidden": "true" }, num[1]), h("div.gc-body", h("div.gc-text", ...richText(num[2])))));
    } else if (bullet) {
      if (!ul) { ul = h("ul.gc-list"); out.push(ul); }
      ol = null;
      ul.append(h("li", ...richText(bullet[1])));
    } else if (line) {
      ol = null; ul = null;
      out.push(h("p.gc-p", ...richText(line)));
    }
  }
  return h("div.reply-view", ...out);
}
