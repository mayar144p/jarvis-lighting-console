// Macros: command lines (as typed in the command bar) played in one go -
// "1-4 red", "cue 3 go", "master 60" - from the command bar or a button.
// All the lines or none, and one Ctrl+Z undoes the lot.
import { run } from "./actions.js";
import { state } from "./store.js";
import { h, modal, confirmBox } from "./ui.js";

export const macros = () => (state.snap && state.snap.macros) || [];

export function openMacros() {
  const list = h("div.fx-running");
  const draw = () => list.replaceChildren(...(macros().length ? macros().map((m) => h("div.fx-run",
    h("b", m.name), h("small.mono", m.lines.slice(0, 3).join(" · ") + (m.lines.length > 3 ? " …" : "")),
    h("button.btn.small", { onclick: () => run("macro_run", { id: m.id }, { toast: true }) }, "Run"),
    h("button.btn.small.ghost", { onclick: () => edit(m) }, "Edit"),
    h("button.btn.small.ghost", { title: "Make a button that plays it", onclick: () => run("quick_set", { page: 1, slot: "free", button: { kind: "macro", macro: m.id, label: m.name } }, { toast: true }) }, "Button"),
    h("button.btn.small.ghost", { title: "Delete", onclick: async () => {
      if (await confirmBox("Delete macro", `Delete “${m.name}”?`, { ok: "Delete", danger: true })) { await run("macro_delete", { id: m.id }, { toast: true }); draw(); }
    } }, "×"))) : [h("p.muted.small", "No macros yet.")]));
  const edit = (m) => {
    const name = h("input", { type: "text", value: m ? m.name : "", placeholder: "e.g. Walk-in look" });
    const lines = h("textarea.room-words", { rows: 6, placeholder: "one command per line, e.g.\nall dimmer 0\n1-8 blue\n1-8 dimmer 40\ncue 1 go" });
    lines.value = m ? m.lines.join("\n") : "";
    const close = modal({
      title: m ? `Edit ${m.name}` : "New macro",
      body: h("div.stepfx", h("label.field", h("span", "Name"), name), h("label.field", h("span", "Command lines"), lines),
        h("p.muted.small", "The same words as the command bar (type ? there for all of them). Each line is checked when you save.")),
      foot: [h("button.btn", { onclick: () => close() }, "Cancel"),
        h("button.btn.primary", { onclick: async () => {
          const r = await run("macro_save", { id: m ? m.id : undefined, macro: { name: name.value, lines: lines.value } }, { toast: true });
          if (r.ok) { close(); setTimeout(draw, 200); }
        } }, "Save")],
    });
  };
  const close = modal({ title: "Macros", body: h("div.stepfx", list, h("div.row-btns", h("button.btn", { onclick: () => edit(null) }, "+ New macro"))), wide: true });
  draw();
  return close;
}

export function macroCandidates(q) {
  return macros().filter((m) => m.name.toLowerCase().includes(q))
    .map((m) => ({ kind: "action", title: `Macro: ${m.name}`, run: () => run("macro_run", { id: m.id }, { toast: true }) }));
}

