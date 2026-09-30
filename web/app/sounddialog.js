// Sound: listen to the room and let it play the lights.  Meters for level
// and the three bands, the beat and the tempo the room is at; links (a
// sound moves a brightness or the effects' speed) and triggers (a beat, a
// bar or a drop presses a button).  The links and triggers are saved with
// the show; the listening happens on whichever screen switched it on.
import { run } from "./actions.js";
import { state, on, selected } from "./store.js";
import { h, modal, toast } from "./ui.js";
import { listening, startListening, stopListening, soundSupported, onSound } from "./soundin.js";

const SOURCES = [["level", "Loudness"], ["bass", "Bass"], ["mid", "Mids"], ["high", "Highs"], ["beat", "The beat (a pulse)"]];
const EVENTS = [["beat", "each beat"], ["bar", "each bar (the 1)"], ["drop", "a drop"]];
// the links and triggers from the snapshot (it follows every edit); whether
// someone is listening from the live feed
const snd = () => {
  const cfg = (state.snap && state.snap.sound) || { links: [], triggers: [] };
  const live = state.lite && state.lite.sound;
  return live ? { ...cfg, listening: live.listening, from: live.from } : cfg;
};
const buttons = () => ((state.snap && state.snap.quick) || {}).buttons || [];

function meter(label) {
  const bar = h("i");
  const el = h("div.snd-meter", h("span", label), h("b", bar));
  return { el, set: (v) => { bar.style.width = `${Math.round(Math.max(0, Math.min(1, v)) * 100)}%`; } };
}

function targetOptions() {
  const groups = (state.snap && state.snap.groups) || [];
  return [["master", "Everything"], ...groups.map((g) => [`group:${g.n}`, `Group: ${g.name}`]),
    ["heads", `The selected lights${selected().length ? ` (${selected().length})` : ""}`], ["fx_speed", "The effects' speed"]];
}

function linkRow(lk) {
  const src = h("select.select", ...SOURCES.map(([v, l]) => h("option", { value: v }, l)));
  src.value = lk.source;
  const tgt = h("select.select", ...targetOptions().map(([v, l]) => h("option", { value: v }, l)));
  const t = lk.target;
  tgt.value = t.type === "group" ? `group:${t.group}` : t.type;
  if (t.type === "heads") tgt.options[tgt.options.length - 2].textContent = `${t.heads.length} light(s)`;
  const depth = h("input", { type: "range", min: 0, max: 100, value: lk.depth, title: "How much it moves" });
  const gain = h("input", { type: "range", min: 0.25, max: 4, step: 0.25, value: lk.gain, title: "Sensitivity" });
  const onBox = h("input", { type: "checkbox" });
  onBox.checked = lk.on !== false;
  const save = () => {
    const [type, g] = tgt.value.split(":");
    const target = type === "group" ? { type, group: +g } : type === "heads" ? { type, heads: t.type === "heads" ? t.heads : selected() } : { type };
    run("sound_link", { id: lk.id, link: { source: src.value, target, depth: +depth.value, gain: +gain.value, on: onBox.checked } });
  };
  [src, tgt, depth, gain, onBox].forEach((c) => c.addEventListener("change", save));
  return h("div.snd-row", onBox, src, h("span.muted.small", "moves"), tgt,
    h("label.snd-k", "depth", depth), h("label.snd-k", "sensitivity", gain),
    h("button.btn.small.ghost", { title: "Remove", onclick: () => run("sound_link", { id: lk.id, remove: true }) }, "×"));
}

function triggerRow(tr) {
  const quick = buttons();
  const ev = h("select.select", ...EVENTS.map(([v, l]) => h("option", { value: v }, l)));
  ev.value = tr.on;
  const every = h("input", { type: "number", min: 1, max: 64, value: tr.every, title: "Every Nth time" });
  const btn = h("select.select", ...quick.map((b) => h("option", { value: b.id }, b.label || b.id)));
  btn.value = tr.button;
  const save = () => run("sound_trigger", { id: tr.id, trigger: { on: ev.value, every: +every.value || 1, button: btn.value } });
  [ev, every, btn].forEach((c) => c.addEventListener("change", save));
  return h("div.snd-row", h("span.muted.small", "On"), ev, h("span.muted.small", "every"), every, h("span.muted.small", "press"), btn,
    h("button.btn.small.ghost", { title: "Remove", onclick: () => run("sound_trigger", { id: tr.id, remove: true }) }, "×"));
}

export function openSoundDialog() {
  const meters = [meter("Loudness"), meter("Bass"), meter("Mids"), meter("Highs")];
  const beat = h("i.snd-beat");
  const bpm = h("span.mono", "–");
  const status = h("p.muted.small", "");
  const toggle = h("button.btn.primary", "Listen");
  const tempoBox = h("input", { type: "checkbox" });
  const links = h("div.snd-list");
  const triggers = h("div.snd-list");
  const draw = () => {
    const s = snd();
    tempoBox.checked = !!s.tempo;
    toggle.textContent = listening() ? "Stop listening" : "Listen";
    toggle.classList.toggle("primary", !listening());
    status.textContent = listening() ? "Listening on this screen." : s.listening ? `Listening on ${s.from || "another screen"}.`
      : soundSupported() ? "Not listening. Links do nothing until something is listening (the rig is never left dark)."
        : (window.isSecureContext ? "This browser has no microphone access." : "A microphone needs https or the desk computer itself (localhost): listen from there.");
    const key = JSON.stringify([s.links, s.triggers, buttons().length, (state.snap && state.snap.groups || []).length]);
    if (links.dataset.key !== key) {
      links.dataset.key = key;
      links.replaceChildren(...(s.links || []).map(linkRow), ...(s.links && s.links.length ? [] : [h("p.muted.small", "No links yet.")]));
      triggers.replaceChildren(...(s.triggers || []).map(triggerRow), ...(s.triggers && s.triggers.length ? [] : [h("p.muted.small", "No triggers yet.")]));
    }
  };
  toggle.addEventListener("click", async () => {
    try {
      if (listening()) stopListening(); else await startListening();
    } catch (e) {
      toast(e.message || String(e), "bad");
    }
    draw();
  });
  tempoBox.addEventListener("change", () => run("sound_tempo", { state: tempoBox.checked }, { toast: true }));
  const offSound = onSound((r) => {
    meters[0].set(r.level); meters[1].set(r.bass); meters[2].set(r.mid); meters[3].set(r.high);
    beat.classList.toggle("on", performance.now() - r.beatAt < 120);
    bpm.textContent = r.bpm ? `${r.bpm} BPM (${Math.round(r.confidence * 100)}% sure)` : "finding the beat…";
  });
  const offSnap = on("snapshot", draw);
  const offLite = on("lite", draw);
  const addLink = () => run("sound_link", { link: { source: "bass", target: selected().length ? { type: "heads", heads: selected() } : { type: "master" }, depth: 60, gain: 1 } }, { toast: true });
  const addTrigger = () => {
    const quick = buttons();
    if (!quick.length) { toast("Make a button first (Buttons page), then a beat or a drop can press it.", "bad"); return; }
    run("sound_trigger", { trigger: { on: "drop", button: quick[0].id, every: 1 } }, { toast: true });
  };
  const body = h("div.snd",
    h("div.row-btns", toggle, status),
    h("div.snd-meters", ...meters.map((m) => m.el), h("div.snd-bpm", beat, bpm)),
    h("label.check", tempoBox, h("span", "The room's beat sets the tempo (when no MIDI clock or CDJ does)")),
    h("h3", "Links: the sound moves…"),
    h("p.muted.small", "Bass pumping the dance floor, the highs on the strobes, loudness on everything; depth 100 goes from dark to full with the music, 30 breathes."),
    links, h("div.row-btns", h("button.btn.small", { onclick: addLink }, "+ Add a link")),
    h("h3", "Triggers: on the beat or the drop, press a button"),
    triggers, h("div.row-btns", h("button.btn.small", { onclick: addTrigger }, "+ Add a trigger")));
  const close = modal({ title: "Sound", body, wide: true, onClose: () => { offSound(); offSnap(); offLite(); } });
  draw();
  return close;
}
