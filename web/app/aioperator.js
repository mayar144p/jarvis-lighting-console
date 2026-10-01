// The AI operator: the AI runs the lights live with the music - a change
// every phrase, and on the drop.  Touch the desk (a cue, a button, a fader,
// the programmer) or press "I've got it" and you have them back.
import { post } from "./api.js";
import { on } from "./store.js";
import { h, modal, toast } from "./ui.js";

const WHY = { on: "on", start: "start", phrase: "phrase", drop: "drop", stop: "stop" };

export async function openOperator() {
  const brief = h("textarea.input", { rows: 3, maxlength: 400,
    placeholder: "e.g. techno night, dark and moody, cold colours; keep strobes for the drops; never strobe the bar" });
  const bars = h("select.select", ...[8, 16, 32].map((b) => h("option", { value: b }, `every ${b} bars`)));
  bars.value = "16";
  const drops = h("input", { type: "checkbox", checked: true });
  const go = h("button.btn.primary", "Let the AI run the lights");
  const status = h("p.small");
  const log = h("div.op-log");
  let cur = {};

  const draw = (o) => {
    cur = o || cur;
    go.textContent = cur.on ? "I've got it" : "Let the AI run the lights";
    go.classList.toggle("primary", !cur.on);
    go.classList.toggle("danger", !!cur.on);
    status.textContent = cur.on
      ? (cur.busy ? "Thinking about the next change…" : cur.bars_left != null ? `Next change in ${cur.bars_left} bar(s)` : "Running")
        + (cur.error ? ` · ${cur.error}` : "")
      : (cur.stopped ? `Stopped: ${cur.stopped}.` : "Off.");
    log.replaceChildren(...(cur.log || []).map((e) => h("div",
      h("span.why", WHY[e.why] || e.why), " ", e.ok ? "" : "✕ ", e.text,
      h("span.muted", ` · ${e.ago_s} s ago`))));
  };
  const refresh = async () => { try { const r = await post("/api/console/assistant", { operator: "status" }); if (r.operator) draw(r.operator); } catch (e) { /* next time */ } };

  go.addEventListener("click", async () => {
    go.disabled = true;
    try {
      const r = cur.on
        ? await post("/api/console/assistant", { operator: "stop" })
        : await post("/api/console/assistant", { operator: "start", brief: brief.value, bars: +bars.value, drops: drops.checked });
      if (!r.ok) toast(r.error || "couldn't start", "bad");
      if (r.operator) {
        draw(r.operator);
        if (r.operator.on && cur.brief) brief.value = cur.brief;
      }
    } finally { go.disabled = false; }
  });

  const timer = setInterval(refresh, 1500);
  const off = on("lite", (l) => { if (l.ai_operator && l.ai_operator.on !== cur.on) refresh(); });
  const body = h("div.ap",
    h("p.muted.small", "The AI runs the lights live with the music: every phrase (on the beat clock - tap it, MIDI clock, the CDJs or the room) "
      + "and on a drop, it makes one change that suits the moment. Touch the desk - a cue, a button, a fader, the programmer - or press "
      + "“I've got it”, and you have them back at once. It never arms or fires effects."),
    h("label.field", h("span", "What kind of night (optional)"), brief),
    h("div.form-grid", h("label.field", h("span", "A change"), bars)),
    h("label.check", drops, h("span", "React to the drop straight away (needs Sound listening)")),
    h("div.row-btns", go), status, log);
  modal({ title: "AI operator", body, onClose: () => { clearInterval(timer); off(); } });
  refresh().then(() => { if (cur.brief) brief.value = cur.brief; if (cur.bars) bars.value = String(cur.bars); });
}
