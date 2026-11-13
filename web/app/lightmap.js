// The light map's scale and its reading (View >> Light map): the 3D paints
// every surface by the lux the rig puts on it (web/js/stage/luxmap.js);
// this is the key to the colours, and a light meter where the pointer is.
import { $, h } from "./ui.js";
import { LUX_STOPS, luxGradientCss, luxScale, luxText } from "/js/stage/luxmap.js";

let box = null;
let stop = null;

const KIND = { floor: "floor", wall: "wall", stage: "stage", ceiling: "ceiling", person: "a person", performer: "a performer",
  object: "an object", box: "an object" };

export const lightMapOn = (stage) => !!(stage && stage.options.lightMap);

/** The light map on or off (`force`), with its scale on the 3D view. */
export function toggleLightMap(stage, force) {
  if (!stage) return;
  const on = force === undefined ? !lightMapOn(stage) : !!force;
  stage.setOptions({ lightMap: on });
  if (stop) { stop(); stop = null; }
  if (box) { box.remove(); box = null; }
  if (!on) return;
  const idle = "Point at the floor, a wall, the stage or a person";
  const read = h("div.lux-read", { "aria-live": "polite" }, idle);
  // (2,000 left unlabelled: the top of a log scale is crowded)
  const ticks = h("div.lux-ticks", ...LUX_STOPS.filter(([lux]) => lux !== 2000).map(([lux]) =>
    h("span", { style: { bottom: `${(100 * luxScale(lux)).toFixed(1)}%` } }, luxText(lux))));
  box = h("div.lux-legend", { role: "group", "aria-label": "Light map" },
    h("div.lux-head", h("b", "Light map"), h("span.muted", "lux"),
      h("button.x", { type: "button", title: "Close the light map", "aria-label": "Close the light map",
        onclick: () => toggleLightMap(stage, false) }, "×")),
    h("div.lux-scale", h("div.lux-bar", { style: { background: luxGradientCss() } }), ticks),
    read,
    h("p.lux-note", "The light the rig puts on each surface now, from every light's real output. "
      + "Lines mark each step. Not counted: the house lights."));
  const wrap = $("#stage-wrap") || document.body;
  wrap.append(box);
  // under the 3D's toolbar, however many rows it has wrapped to
  const place = () => {
    const bar = $("#vis-bar");
    if (!box || !bar) return;
    const top = bar.getBoundingClientRect().bottom - wrap.getBoundingClientRect().top;
    box.style.top = `${Math.max(8, Math.round(top + 8))}px`;
  };
  place();
  window.addEventListener("resize", place);
  // the reading: where the pointer is, again as the lights change
  let last = null;
  const show = () => {
    if (!last || !box) return;
    const hit = stage.pointAt(last);
    if (!hit) { read.textContent = idle; return; }
    const lux = stage.luxAt(hit.point, hit.normal);
    read.replaceChildren(h("b", `${luxText(lux)} lux`), ` on ${KIND[hit.kind] || "the " + hit.kind}`);
  };
  let at = 0;
  const move = (ev) => {
    last = { clientX: ev.clientX, clientY: ev.clientY };
    const now = performance.now();
    if (now - at < 60) return;
    at = now;
    show();
  };
  const leave = () => { last = null; read.textContent = idle; };
  const dom = stage.renderer && stage.renderer.domElement;
  if (dom) {
    dom.addEventListener("pointermove", move);
    dom.addEventListener("pointerleave", leave);
  }
  const timer = setInterval(() => { if (!document.hidden) show(); }, 400);
  stop = () => {
    clearInterval(timer);
    window.removeEventListener("resize", place);
    if (dom) {
      dom.removeEventListener("pointermove", move);
      dom.removeEventListener("pointerleave", leave);
    }
  };
}
