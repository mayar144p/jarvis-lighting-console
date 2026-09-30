// Colour tab -> Match colours…: LEDs differ between brands, so the same
// "red" looks orange on one and pink on another.  Put a reference light and
// this model side by side on the same colour, then take each emitter down
// until they match.  Saved with the fixture: every light of this model (and
// mode) comes out matched, in cues, effects and buttons alike.
import { act } from "./api.js";
import { run } from "./actions.js";
import { selectionHeads } from "./store.js";
import { h, modal, toast, throttle } from "./ui.js";

const LABEL = { red: "Red", green: "Green", blue: "Blue", white: "White", amber: "Amber", uv: "UV", lime: "Lime", cyan: "Cyan", magenta: "Magenta", yellow: "Yellow" };
const TESTS = [["#ffffff", "White"], ["#ff0000", "Red"], ["#ff8000", "Amber"], ["#00ff00", "Green"], ["#0000ff", "Blue"], ["#ff00ff", "Magenta"], ["#00ffff", "Cyan"]];

export async function openColourMatch() {
  const sel = selectionHeads();
  if (!sel.length) { toast("Select a light of the model to match"); return; }
  const first = sel[0];
  const r = await act("colour_cal_get", { head: first.head_no });
  if (!r.ok) { toast(r.error || "Can't match this light", "bad"); return; }
  if (!r.emitters.length) { toast("This light has no colour emitters to match", "bad"); return; }
  const cal = { ...r.cal };
  const send = throttle((role, v) => run("colour_cal", { head: first.head_no, [role]: v }, { silentError: true }), 120);
  const rows = r.emitters.map((role) => {
    const out = h("output.mono", `${cal[role]}%`);
    const s = h("input", { type: "range", min: 40, max: 100, step: 1, value: cal[role] });
    s.addEventListener("input", () => { cal[role] = +s.value; out.textContent = `${s.value}%`; send(role, +s.value); });
    return h("label.cm-row", h("span", LABEL[role] || role), s, out);
  });
  const tests = h("div.chip-row", h("span.muted.small", "Show:"), ...TESTS.map(([hex, name]) =>
    h("button.chip", { title: `Every selected light on ${name.toLowerCase()} - put a reference light on it too`, onclick: () => run("set_colour", { hex }) },
      h("i.slot-dot", { style: { background: hex } }), name)));
  const same = selectionHeads().filter((x) => x.model === first.model && x.mode === first.mode).length;
  const body = h("div.colour-match",
    h("p.muted.small", `${first.model} (${first.mode}) - applies to every light of this model. Put a light of another brand on the same colour next to it, then take each colour down until the two look the same. Start with white.`),
    tests, ...rows,
    h("p.muted.small", `${same} of the selected light(s) are this model. The 3D view keeps showing the colour you ask for; the real lights get the matched one.`));
  const close = modal({
    title: "Match colours", body,
    foot: [h("button.btn.ghost", { onclick: async () => {
      await run("colour_cal", { head: first.head_no, reset: true }, { toast: true });
      close();
    } }, "Reset to as it comes"), h("span.grow"), h("button.btn.primary", { onclick: () => close() }, "Done")],
  });
}
