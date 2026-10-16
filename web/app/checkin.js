// Venue check-in: the show was made for one rig, the venue has its own.
// Ask the lights (RDM), line them up against the show, and for each light
// of the show that isn't here offer the one that is ("your #5 wash: use the
// SlimPAR at 1.033") - it takes the light's place: its number, groups, cues
// and songs stay, its type and address change (one Ctrl+Z each).
import { h, modal, toast } from "./ui.js";
import { post } from "./api.js";
import { run } from "./actions.js";
import { state } from "./store.js";
import { pairUp } from "./rigmatch.js";

export async function openCheckin() {
  const body = h("div.checkin", h("p.muted", "Asking the lights here… (a few seconds)"));
  const close = modal({ title: "Venue check-in", wide: true, body });
  const d = await post("/api/console/rdm", {}).catch((e) => ({ error: e.message }));
  const r = d.result || {};
  const patch = (state.snap && state.snap.patch) || [];
  const byNo = new Map(patch.map((x) => [x.head_no, x]));
  if (d.error || !r.ok || !(r.devices || []).length) {
    body.replaceChildren(
      h("p", (d.error || r.error) ? `RDM failed: ${d.error || r.error}` : "No light answered."),
      h("p.muted.small", "Check-in asks the lights over RDM: it needs a node with RDM on and lights that support it. "
        + "Without it, match the rig by hand: the DMX map shows every address, and a light's ⋯ → Change fixture type… "
        + "swaps it for the venue's model (its cues and songs carry over)."),
      h("div.row-btns",
        h("button.btn", { onclick: () => import("./fixtures.js").then((m) => m.openDmxMap()) }, "DMX map…"),
        h("button.btn", { onclick: () => import("./fixtures.js").then((m) => m.scanRig()) }, "Find nodes"),
        h("button.btn", { onclick: () => { close(); import("./dialogs.js").then((m) => m.openAddDialog()); } }, "Add lights…")));
    return;
  }
  const devs = r.devices || [];
  const ok = devs.filter((x) => x.status === "ok");
  const differ = devs.filter((x) => x.status === "different");
  const found = devs.filter((x) => x.status === "new");
  const silent = (r.silent || []).map((s) => ({ ...s, ...(byNo.get(s.head) || {}), head: s.head }));
  const pairs = pairUp(silent, found);
  const paired = new Set(pairs.map((p) => p.dev.uid));
  const loose = found.filter((x) => !paired.has(x.uid));
  const alone = silent.filter((s) => !pairs.some((p) => p.head.head === s.head));
  const addr = (x) => `${x.universe}.${String(x.address).padStart(3, "0")}`;
  const use = (s, dev) => run("stand_in", { head: s.head, query: dev.name, universe: dev.universe, address: dev.address }, { toast: true });
  const choose = (s) => {
    const pick = h("select.select.small", h("option", { value: "" }, "…another light found here"),
      ...found.map((x, i) => h("option", { value: i }, `${x.name} at ${addr(x)}`)));
    pick.addEventListener("change", () => { if (pick.value !== "") use(s, found[+pick.value]); });
    return pick;
  };
  body.replaceChildren(
    h("p", h("b", `${ok.length} of ${patch.length} light${patch.length === 1 ? "" : "s"} found as patched`),
      h("span.muted", ` · ${found.length} other light(s) here · ${silent.length} of the show's not heard`)),
    pairs.length ? h("h3", "Lights to swap in") : null,
    ...pairs.map(({ head: s, dev }) => h("div.rdm-row.different",
      h("span.ready-ic", "⇄"),
      h("div", h("b", `#${s.head} ${s.name || s.model || ""}`), h("div.small", `isn't here. ${dev.name} at ${addr(dev)} is the same kind of light.`)),
      h("div.row-btns", h("button.btn.small.primary", { onclick: () => use(s, dev) }, "Use it"), choose(s)))),
    differ.length ? h("h3", "Patched here, but a different light") : null,
    ...differ.map((x) => h("div.rdm-row.different", h("span.ready-ic", "!"),
      h("div", h("b", x.name), h("div.small", x.note)),
      h("div.row-btns", h("button.btn.small", { onclick: () => use({ head: x.head }, x) }, "Change it to this light")))),
    alone.length ? h("h3", "The show's lights not heard here") : null,
    ...alone.map((s) => h("div.rdm-row.silent", h("span.ready-ic", "?"),
      h("div", h("b", `#${s.head} ${s.name || ""}`), h("div.small", "no answer, and no light of its kind found (no RDM? off? another address?)")),
      h("div.row-btns", found.length ? choose(s) : null,
        h("button.btn.small.ghost", { onclick: () => run("park", { heads: [s.head], mode: "dark" }, { toast: true }) }, "Park it dark")))),
    loose.length ? h("h3", "Other lights here") : null,
    ...loose.map((x) => h("div.rdm-row.new", h("span.ready-ic", "+"),
      h("div", h("b", x.name), h("div.small", `${addr(x)}${x.footprint ? ` · ${x.footprint} ch` : ""}`)),
      h("div.row-btns", h("button.btn.small", { onclick: () => run("add_heads", { query: x.name, universe: x.universe, address: x.address }, { toast: true }) }, "Add it")))),
    h("div.row-btns", { style: { marginTop: "14px" } },
      h("button.btn", { onclick: () => { close(); import("./dialogs.js").then((m) => m.openReadyCheck()); } }, "Ready check…"),
      h("span.muted.small", "Swapped lights keep their number, groups, cues and songs; Ctrl+Z undoes each one.")));
  if (!pairs.length && !differ.length && !alone.length && !loose.length) toast("Every light here is as the show expects", "ok");
}
