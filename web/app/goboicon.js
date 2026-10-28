// Gobo icons: each gobo button shows the gobo itself, as the light throws
// it - its real picture (the fixture file's, or the maker's own GDTF) drawn
// white on a black disc, the way the 3D projects it.  Open is a clear disc;
// a gobo the file names without a picture gets a dotted stand-in.
import { h } from "./ui.js";
import { token } from "./api.js";
import { patch, selected } from "./store.js";

const SIZE = 48;
const made = new Map();             // ref -> Promise<data URL | null>

// the picture for a slot: its own, else the selected light's gobo list
// (filled from the maker's GDTF or the twin file) at the same DMX range
export function goboRef(role, s) {
  if (s.img) return s.img;
  if (/\bopen\b|no gobo/i.test(s.name || "")) return "";
  const key = role === "gobo2" ? "gobos2" : "gobos";
  const sel = new Set(selected());
  for (const hd of patch()) {
    if (!sel.has(hd.head_no)) continue;
    const row = (hd[key] || []).find((g) => g[0] === s.from);
    if (row) return row[2];
  }
  return "-";
}

function picture(ref) {
  if (!made.has(ref)) {
    made.set(ref, (async () => {
      const t = token();
      const r = await fetch("/api/console/gobo?ref=" + encodeURIComponent(ref), { headers: t ? { "X-Jarvis-Token": t } : {} });
      if (!r.ok) return null;
      const url = URL.createObjectURL(await r.blob());
      try {
        const img = new Image();
        await new Promise((ok, bad) => { img.onload = ok; img.onerror = bad; img.src = url; });
        const c = document.createElement("canvas");
        c.width = c.height = SIZE;
        const g = c.getContext("2d", { willReadFrequently: true });
        g.drawImage(img, 0, 0, SIZE, SIZE);
        const d = g.getImageData(0, 0, SIZE, SIZE), px = d.data;
        // as the 3D reads it: an outline drawing lets light through where
        // it is clear, a glass picture by how bright it is
        let clear = 0;
        for (let i = 3; i < px.length; i += 4) if (px[i] < 250) clear++;
        const byAlpha = clear > px.length / 4 * 0.02;
        for (let i = 0; i < px.length; i += 4) {
          const m = byAlpha ? 255 - px[i + 3] : Math.round(0.3 * px[i] + 0.59 * px[i + 1] + 0.11 * px[i + 2]);
          px[i] = px[i + 1] = px[i + 2] = m;
          px[i + 3] = 255;
        }
        g.putImageData(d, 0, 0);
        return c.toDataURL();
      } finally { URL.revokeObjectURL(url); }
    })().catch(() => null));
  }
  return made.get(ref);
}

// <i class="gobo-ic">: filled in when the picture arrives
export function goboIcon(ref, title = "") {
  const el = h("i.gobo-ic", { title, "aria-hidden": "true" });
  if (ref === "") el.classList.add("open");
  else if (!ref || ref === "-") el.classList.add("standin");
  else {
    el.classList.add("loading");
    picture(ref).then((src) => {
      el.classList.remove("loading");
      if (src) el.style.backgroundImage = `url("${src}")`;
      else el.classList.add("standin");
    });
  }
  return el;
}
