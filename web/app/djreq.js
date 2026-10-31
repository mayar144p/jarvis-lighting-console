// DJ / host requests (web/requests.html) on the desk: each one a card -
// Do it (the copilot does it now, live, within the room's rules) or No
// thanks.  The phone sees the answer.  And the link to hand the DJ.
import { post } from "./api.js";
import { state, on } from "./store.js";
import { $, h, modal, toast } from "./ui.js";

let box = null;
let shown = "";

export function initDjRequests() {
  box = h("div.djreq", { "aria-live": "polite" });
  document.body.append(box);
  on("lite", draw);
  on("snapshot", draw);
}

function draw() {
  const list = ((state.lite && state.lite.requests) || (state.snap && state.snap.requests) || []);
  const key = JSON.stringify(list.map((r) => r.id));
  if (key === shown) return;
  shown = key;
  box.replaceChildren(...list.map((r) => {
    const card = h("div.djreq-card",
      h("div.djreq-who", `${r.who} asks`),
      h("div.djreq-text", r.text),
      h("div.djreq-btns",
        h("button.btn.small.primary", { onclick: () => doIt(r, card) }, "Do it"),
        h("button.btn.small.ghost", { onclick: () => answer(r.id, false, "") }, "No thanks")));
    return card;
  }));
}

async function answer(id, yes, note) {
  try { await post("/api/console/requests", { answer: id, yes, note }); } catch (e) { toast(e.message, "bad"); }
}

async function doIt(r, card) {
  card.classList.add("busy");
  const ai = !!(state.status && state.status.llm_configured);
  if (!ai) {
    // no AI: the operator does it; the phone hears it's on its way
    toast(`Do it by hand: “${r.text}”`, "", 6000);
    await answer(r.id, true, "on it");
    return;
  }
  try {
    // the desk words it (their text is data, not instructions) and holds the
    // AI to the lights as they are, within the room's rules
    const res = await post("/api/console/assistant", { dj_request: r.id });
    const said = (res.reply || res.error || "").slice(0, 160);
    await answer(r.id, !!res.ok, said);
    toast(res.ok ? `Done: ${r.text}` : (res.error || "it didn't work"), res.ok ? "ok" : "bad", 5000);
  } catch (e) {
    card.classList.remove("busy");
    toast(e.message, "bad");
  }
}

export async function openRequestsLink() {
  let d = await post("/api/console/requests", {});
  const link = h("input.input", { readonly: true });
  const paint = () => { link.value = `${location.origin}/requests.html?r=${d.code}`; };
  paint();
  modal({
    title: "DJ / host requests",
    body: h("div.djreq-link",
      h("p", "Give the DJ or the host this link: they tap what they'd like (more energy, a speech, a blackout for the intro…) "
        + "and it shows here, on the desk. You tap Do it - the copilot does it, live, within the room's rules - or No thanks."),
      h("p.muted.small", "The link can only send requests, never run the desk. On another device use this computer's network address "
        + "(Desk → Other computers, phones and tablets…) in place of localhost."),
      link,
      h("div.row-btns",
        h("button.btn", { onclick: async () => { try { await navigator.clipboard.writeText(link.value); toast("Link copied", "ok"); } catch { link.select(); } } }, "Copy the link"),
        h("button.btn.ghost", { title: "Phones with the old link can't send any more", onclick: async () => { d = await post("/api/console/requests", { new_code: true }); paint(); toast("New code: the old link stops working", "ok"); } }, "New code"))),
  });
}
