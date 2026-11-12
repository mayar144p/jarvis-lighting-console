// Pick an NDI source on the network (OBS, Resolume, vMix, a camera...):
// the LED screens in the 3D and Paint the rig use it.  The list refreshes
// while open; a name can be typed for a source that isn't on yet.
import { ndiHere, ndiSources, ndiStatus } from "/js/ndi.js";
import { h, modal } from "./ui.js";

export const NDI_HOWTO = "NDI comes with the free NDI Tools (ndi.video/tools). Install them on this computer, then start Haze again.";

/** Resolves the source's name, or null (cancelled). */
export function pickNdiSource(current = "") {
  return new Promise((resolve) => {
    let done = false, timer = 0;
    const finish = (v) => { if (done) return; done = true; clearInterval(timer); resolve(v); close(); };
    const list = h("div.ndi-list", h("p.muted.small", "Looking on the network…"));
    const typed = h("input", { type: "text", value: current, placeholder: "COMPUTER (Source name)", style: { width: "100%" } });
    typed.addEventListener("keydown", (e) => { if (e.key === "Enter" && typed.value.trim()) finish(typed.value.trim()); });
    const note = h("p.muted.small");
    const refresh = async () => {
      if (!ndiHere) {
        list.replaceChildren(h("p", "NDI works in the Haze desktop app (not in a web browser)."));
        return;
      }
      const st = await ndiStatus();
      if (!st.ok) {
        list.replaceChildren(h("p", `NDI isn't ready: ${st.why}.`), h("p.muted.small", NDI_HOWTO));
        return;
      }
      const srcs = await ndiSources();
      if (done) return;
      list.replaceChildren(...(srcs.length
        ? srcs.map((s) => h("button.btn.ndi-src" + (s.name === current ? ".on" : ""), {
          title: s.url ? `at ${s.url}` : "", onclick: () => finish(s.name) }, s.name))
        : [h("p.muted.small", "No NDI sources on the network yet. Start one (OBS with NDI, Resolume, a camera...) - the list updates by itself.")]));
      note.textContent = st.version ? `${st.version}. NDI® is a registered trademark of Vizrt NDI AB.` : "";
    };
    const close = modal({
      title: "Live video from the network (NDI)",
      body: h("div.ndi-pick", list,
        h("label.field", h("span", "Or its name, as the other computer shows it"), typed), note),
      foot: [
        h("button.btn", { onclick: () => finish(null) }, "Cancel"),
        h("button.btn.primary", { onclick: () => typed.value.trim() && finish(typed.value.trim()) }, "Use this name"),
      ],
      onClose: () => finish(null),
    });
    refresh();
    timer = setInterval(refresh, 2000);
  });
}
