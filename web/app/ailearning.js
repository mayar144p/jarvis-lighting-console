// What the AI has learnt about you, and your say over every bit of it
// (app/ailearn.py): notes you asked for and ones it suggested (waiting for
// your OK), habits worked out from your edits to its shows, the requests you
// kept as good examples, and the ones you undid.  Anything can be taken back;
// learning can be paused.
import { post } from "./api.js";
import { h, modal, toast, confirmBox } from "./ui.js";

const call = (body) => post("/api/console/assistant", { learning: true, ...body });

function section(title, hint, items) {
  return h("section.ai-learn-sec",
    h("h4", title), hint ? h("p.muted.small", hint) : null,
    items.length ? h("div.ai-learn-list", ...items) : h("p.muted.small", "Nothing yet."));
}

function row(text, sub, buttons) {
  return h("div.ai-learn-row", h("div.grow", h("div", text), sub ? h("div.muted.small", sub) : null), ...buttons);
}

export async function openLearning() {
  let d = await call({}).catch((e) => ({ error: e.message }));
  if (d.error) { toast(d.error, "bad"); return; }
  const body = h("div.ai-learn");
  const act = async (b) => { d = await call(b).catch((e) => ({ error: e.message })); if (d.error) toast(d.error, "bad"); else draw(); };
  const btn = (label, title, b, cls = "") => h("button.btn.small" + cls, { title, onclick: () => act(b) }, label);
  const add = h("input.input", { placeholder: "Something to remember (e.g. warm white for speeches)", maxlength: 200 });
  function draw() {
    body.replaceChildren(
      h("label.check", h("input", { type: "checkbox", checked: !!d.paused, onchange: (e) => act({ op: "pause", on: e.target.checked }) }),
        " Pause learning - nothing new is learnt; what's below still applies"),
      section("Waiting for your OK", "Things the AI noticed and would like to remember. Only what you keep is used.",
        (d.pending || []).map((n) => row(n.text, n.replaces ? "Replaces an older note that says much the same" : "Suggested by the AI",
          [btn("Keep", "Remember this", { op: "keep", id: n.id }, ".primary"), btn("No", "Don't remember this", { op: "drop", id: n.id })]))),
      section("What it remembers", "Used in every request. Your own and the ones you OK'd.",
        (d.notes || []).map((n) => row(n.text, n.source === "you" ? "You asked for this" : "Suggested by the AI, kept by you",
          [btn("Forget", "Forget this note", { op: "drop", id: n.id })]))),
      h("div.row-btns", add, h("button.btn.small", { onclick: () => { const t = add.value.trim(); if (t) { add.value = ""; act({ op: "add", text: t }); } } }, "Add")),
      section("Habits from your edits", "The same change made to two or more of the AI's shows: it does it your way in new shows. "
        + "Worked out from the shows as they are - undo the edit and the habit goes.",
        (d.habits || []).map((x) => row(x.text, `${x.strength || ""} - ${x.shows.join(", ")}${(x.against || []).length ? ` (kept as it was in ${x.against.join(", ")})` : ""}`, [btn("Forget", "Not a habit - don't do this in new shows", { op: "forget_habit", id: x.text })]))),
      section("One show's own choices", "Kept to that show; the AI asks before carrying them to another.",
        (d.one_off || []).slice(0, 12).map((x) => row(x.text, `${x.show} (${x.page})`, []))),
      section("Kept examples", "Requests you kept (👍): the AI is shown one like it with a new request.",
        (d.examples || []).map((x) => row(`“${x.prompt}”`, (x.how || []).slice(0, 3).join(" → "), [btn("Un-keep", "Not a good example after all", { op: "unkeep", id: x.id })]))),
      section("Recently undone", "Your Ctrl+Z on the AI's work (last month): for a request like it, it does it differently.",
        (d.undone || []).map((x) => row(`“${x.prompt}”`, x.at || "", [btn("Forget", "That undo wasn't about the AI's choice", { op: "forget_undo", id: x.id })]))));
  }
  draw();
  modal({
    title: "What the AI has learned", body, wide: true,
    foot: [h("button.btn.ghost.danger", { onclick: async () => {
      if (await confirmBox("Start again", "Forget every note and suggestion, and every forgotten habit and undo? Kept examples and your shows stay.", { ok: "Forget", danger: true })) act({ op: "forget_all" });
    } }, "Forget all notes…")],
  });
}
