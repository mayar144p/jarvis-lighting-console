// Settings -> AI (backlog A12): the switch (Online / Local / Auto), the
// online key, and the desk's own offline AI - download (pause / resume), an
// AI pack from a USB stick, remove.  The key is sent once and never shown.
import { get, post } from "./api.js";
import { h, toast } from "./ui.js";

const MODES = [
  ["online", "Online", "Gemini: smarter, needs internet, has daily limits"],
  ["local", "Local", "The offline AI on this computer: unlimited, no internet"],
  ["auto", "Auto", "Gemini first; at its limit or with no internet, the offline AI"],
];
const gb = (n) => `${(n / 2 ** 30).toFixed(1)} GB`;

/** The AI switch as a small menu (the copilot's header). */
export async function setAiMode(mode) {
  const d = await post("/api/ai", { mode }).catch((e) => ({ error: e.message }));
  if (d.error) { toast(d.error, "bad"); return null; }
  toast(`AI: ${MODES.find((m) => m[0] === mode)[1]}`, "ok");
  return d;
}
export const aiModes = MODES;

/** The AI page of Settings. */
export function aiPanel() {
  const root = h("div.ai-panel", h("p.muted.small", "Loading…"));
  let timer = 0;
  const render = (d) => {
    clearTimeout(timer);
    const off = d.offline_ai || {};
    const dl = off.download || {};
    const sug = off.suggest || {};
    const pick = (off.catalog || []).find((m) => m.id === sug.id) || (off.catalog || [])[0] || {};
    const seg = h("div.seg", ...MODES.map(([k, label, hint]) => h("button" + (d.mode === k ? ".on" : ""), {
      title: hint, onclick: async () => { const r = await setAiMode(k); if (r) refresh(); } }, label)));
    const key = h("input", { type: "password", autocomplete: "off", placeholder: d.has_key ? "a key is saved - type to replace it" : "paste your Gemini API key" });
    const model = h("input", { type: "text", value: d.model || "" });
    const ollama = h("input", { type: "text", value: d.local_url || "", placeholder: "http://127.0.0.1:11434/v1" });
    const pack = h("input", { type: "text", placeholder: "the AI pack's path, e.g. E:\\qwen3-8b.gguf" });
    const save = async (body) => {
      const r = await post("/api/ai", body).catch((e) => ({ error: e.message }));
      if (r.error) toast(r.error, "bad"); else { toast("Saved", "ok"); refresh(); }
    };
    const local = async (body) => {
      const r = await post("/api/ai/local", body).catch((e) => ({ error: e.message }));
      if (r.error) toast(r.error, "bad");
      refresh();
    };
    const pct = dl.total ? Math.min(100, (dl.done || 0) * 100 / dl.total) : 0;
    const dlRow = dl.running || dl.paused || dl.error
      ? h("div.ai-dl",
        h("div.ai-bar", h("i", { style: { width: pct.toFixed(1) + "%" } })),
        h("div.row-btns",
          h("span.muted.small", dl.error ? dl.error
            : `${dl.file || "the model"}: ${gb(dl.done || 0)} of ${dl.total ? gb(dl.total) : "?"}${dl.paused && !dl.running ? " - paused" : ""}`),
          dl.running ? h("button.btn.small", { onclick: () => local({ pause: true }) }, "Pause")
            : h("button.btn.small", { onclick: () => local({ download: dl.id || pick.id }) }, dl.error ? "Try again" : "Resume")))
      : null;
    root.replaceChildren(
      h("h3", "Which AI"), seg,
      h("p.muted.small", (MODES.find((m) => m[0] === d.mode) || [, , ""])[2]
        + (d.last_note ? ` · last time: ${d.last_note}` : "")),
      h("h3", "Online (Gemini)"),
      h("div.form-grid",
        h("label.field", h("span", "API key"), key),
        h("label.field", h("span", "Model"), model)),
      h("div.row-btns", h("button.btn", { onclick: () => save({ ...(key.value.trim() ? { key: key.value.trim() } : {}), model: model.value.trim() }) }, "Save"),
        h("span.muted.small", "Free key: aistudio.google.com. It stays on this computer, never in a show or a bug report.")),
      h("h3", "Offline AI (on this computer)"),
      h("p.muted.small", d.local_running
        ? `Running: ${(d.local_models || []).join(", ")}`
        : (off.models || []).length ? "Ready - it starts by itself the first time the copilot needs it (the first answer takes a minute)."
          : "Not installed yet."),
      ...(off.models || []).map((m) => h("div.row-btns", h("span", m.file), h("span.muted.small", gb(m.bytes)),
        h("button.btn.small.danger", { onclick: () => local({ remove: m.file }) }, "Remove"))),
      off.runtime && !(off.models || []).length && !dl.running ? h("div.row-btns",
        h("button.btn.primary", { disabled: !sug.ok, onclick: () => local({ download: pick.id }) },
          `Download ${pick.label || "the offline AI"} (${pick.gb || "?"} GB)`),
        h("span.muted.small", sug.ok ? `${pick.note || ""} · this computer: ${sug.ram_gb} GB memory, ${sug.free_gb} GB free`
          : `Not on this computer: ${sug.why}`)) : null,
      dlRow,
      off.runtime ? h("div.row-btns", pack, h("button.btn", { onclick: () => pack.value.trim() && local({ import: pack.value.trim() }) }, "Use an AI pack"))
        : h("p.muted.small", "The offline AI comes with the desktop app. Here in the browser, run Ollama or LM Studio on this computer and put its address below."),
      h("details", { open: !off.runtime }, h("summary.muted.small", "Use Ollama or LM Studio instead"),
        h("div.row-btns", ollama, h("button.btn", { onclick: () => save({ local_url: ollama.value.trim() }) }, "Save"))));
    if (dl.running) timer = setTimeout(refresh, 1000);
  };
  const refresh = () => get("/api/ai").then((d) => { if (root.isConnected || !root.parentNode) render(d); })
    .catch((e) => root.replaceChildren(h("p.muted.small", e.message)));
  refresh();
  return root;
}
