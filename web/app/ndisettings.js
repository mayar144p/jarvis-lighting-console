// Settings -> Video (NDI): the 3D view out as an NDI source (a media
// server, an LED processor, OBS for a stream can take it), and what NDI
// video is on the network to show on the screens and the lights.
import { ndiHere, ndiSources, ndiStatus, ndiSetOut, ndiOnOut } from "/js/ndi.js";
import { NDI_HOWTO } from "./ndipick.js";
import { h, toast } from "./ui.js";

const SIZES = [["960x540", "960 × 540 (light on the computer)"], ["1280x720", "1280 × 720 (HD)"], ["1920x1080", "1920 × 1080 (Full HD)"]];
const RATES = [25, 30, 50, 60];

export function ndiPanel() {
  const box = h("div.ndi-panel");
  if (!ndiHere) {
    box.append(h("p.muted.small", "NDI (video over the network, in and out) works in the Haze desktop app."));
    return box;
  }
  const line = h("p.muted.small", "Checking NDI on this computer…");
  const on = h("input", { type: "checkbox" });
  const name = h("input", { type: "text", maxlength: 60, placeholder: "Haze 3D", style: { width: "14em" } });
  const size = h("select.select", ...SIZES.map(([v, l]) => h("option", { value: v }, l)));
  const fps = h("select.select", ...RATES.map((r) => h("option", { value: String(r) }, `${r} a second`)));
  const live = h("p.muted.small");
  const srcs = h("div.ndi-srcs");
  let timer = 0;

  const apply = async (cfg) => {
    const r = await ndiSetOut(cfg);
    if (r && r.ok === false && cfg.on !== false) toast(`NDI out: ${r.why}`, "bad");
    refresh();
  };
  on.addEventListener("change", () => apply({ on: on.checked, name: name.value.trim() || "Haze 3D", size: size.value, fps: +fps.value }));
  name.addEventListener("change", () => apply({ name: name.value.trim() || "Haze 3D" }));
  size.addEventListener("change", () => apply({ size: size.value }));
  fps.addEventListener("change", () => apply({ fps: +fps.value }));

  async function refresh() {
    if (!box.isConnected && timer) { clearInterval(timer); timer = 0; return; }
    const st = await ndiStatus();
    if (!st.ok) {
      line.replaceChildren(h("b", "NDI isn't ready: "), `${st.why}. `, NDI_HOWTO);
      on.disabled = true;
      return;
    }
    on.disabled = false;
    line.textContent = `${st.version || "NDI"} is on this computer. NDI® is a registered trademark of Vizrt NDI AB.`;
    const s = st.settings || {};
    if (document.activeElement !== name) name.value = s.name || "Haze 3D";
    on.checked = !!s.on;
    size.value = s.size || "1280x720";
    fps.value = String(s.fps || 30);
    if (st.out) {
      live.textContent = st.sending
        ? `Sending “${st.out.name}”${st.out.size ? ` at ${st.out.size[0]} × ${st.out.size[1]}` : ""} · ${st.out.watching ? `${st.out.watching} watching` : "no one watching yet"} · ${st.out.frames} frames`
        : `“${st.out.name}” is on the network - open a 3D view (the 3D window, or the 3D in a desk window) and it sends what that shows.`;
    } else live.textContent = s.on ? "Starting…" : "";
    const list = await ndiSources();
    const inUse = new Set((st.inputs || []).map((i) => i.name));
    srcs.replaceChildren(...(list.length
      ? list.map((x) => h("div.ndi-src-row", h("span", x.name), inUse.has(x.name) ? h("span.chip", "in use") : null))
      : [h("p.muted.small", "No NDI sources on the network right now.")]));
  }

  box.append(line,
    h("h3", "The 3D out"),
    h("label.check", on, h("span", "Send the 3D view as an NDI source")),
    h("div.form-grid",
      h("label.field", h("span", "Its name on the network"), name),
      h("label.field", h("span", "Size"), size),
      h("label.field", h("span", "Pictures"), fps)),
    live,
    h("p.muted.small", "What the 3D window shows goes out (or the 3D in a desk window when that's all that's open); labels and buttons don't. A media server, an LED processor or OBS takes it like any NDI source."),
    h("h3", "NDI video on the network"),
    h("p.muted.small", "Show one on an LED screen in the 3D (Venue: a screen's Shows → Live video from the network), or lay it over the lights (FX → Paint the rig → + NDI source)."),
    srcs);
  ndiOnOut(() => refresh());
  refresh();
  timer = setInterval(refresh, 2000);
  return box;
}
