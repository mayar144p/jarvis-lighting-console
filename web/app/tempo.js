// The beat clock in the top bar: the tempo, where the beat is (a light that
// flashes on each beat, brighter on the 1), and where it comes from - taps,
// a typed BPM, MIDI clock, the CDJs (Pro DJ Link) or this browser's MIDI.
// Tap the pill (or T) on the beat; Shift+T says "this is the 1".
import { run } from "./actions.js";
import { state, on } from "./store.js";
import { $, h, menu, promptBox } from "./ui.js";

const SRC = { manual: "", tap: "tap", midi: "MIDI", prodj: "CDJ", browser: "MIDI", audio: "audio" };
let recv = { t: 0, beats: 0, bpm: 120 };        // the last word from the desk, in this page's clock
let raf = 0;

export const tempo = () => (state.lite && state.lite.tempo) || (state.snap && state.snap.tempo) || null;

function sync() {
  const t = tempo();
  if (!t) return;
  // beats since some zero, at the moment the lite arrived: the pill counts on
  // from there by itself, so the light flashes on the beat between updates
  recv = { t: performance.now(), beats: (t.beat - 1) + (t.phase || 0), bpm: t.bpm };
  $("#tempo-bpm").textContent = t.bpm.toFixed(t.bpm % 1 ? 1 : 0);
  const src = SRC[t.source] || "";
  $("#tempo-src").textContent = src && (t.live || t.source === "tap") ? src : "";
  $("#tempo").classList.toggle("live", !!t.live);
  const snd = (state.lite && state.lite.sound) || (state.snap && state.snap.sound) || {};
  $("#tempo").classList.toggle("hearing", !!snd.listening);
  const auto = (state.lite && state.lite.autopilot) || (state.snap && state.snap.autopilot) || {};
  $("#tempo").classList.toggle("auto", !!auto.on);
  $("#tempo").title = `${t.bpm.toFixed(1)} BPM${src ? " from " + (t.source === "prodj" && t.deck ? t.deck.name || "the CDJs" : src) : ""}`
    + ` · tap on the beat (T), Shift+T for the 1 · effects locked to the beat follow it`
    + (t.follow ? " · the Speed master follows it" : "");
}

function tick() {
  raf = requestAnimationFrame(tick);
  const beats = recv.beats + (performance.now() - recv.t) / 1000 * recv.bpm / 60;
  const phase = beats - Math.floor(beats);
  const one = Math.floor(beats) % 4 === 0;
  const dot = $("#tempo-dot");
  const lit = phase < 0.18;
  dot.classList.toggle("on", lit);
  dot.classList.toggle("one", lit && one);
}

export function tap() {
  const el = $("#tempo");
  el.classList.add("tapped");
  setTimeout(() => el.classList.remove("tapped"), 90);
  return run("tempo_tap", {}, { silentError: true });
}

export const downbeat = () => run("tempo_sync", { beat: 1 }, { toast: "Beat 1 is now" });

function openMenu(anchor) {
  const t = tempo() || {};
  menu(anchor, [
    { label: "Tap on the beat", hint: "T", run: tap },
    { label: "This is the 1 (downbeat)", hint: "Shift+T", run: downbeat },
    { label: "Type the BPM…", run: async () => {
      const v = await promptBox("Tempo", "BPM", (t.bpm || 120).toFixed(1), { ok: "Set" });
      if (v) run("tempo_set", { bpm: +v }, { toast: true });
    } },
    { label: "Double  ×2", hint: "it heard half-time", run: () => run("tempo_set", { bpm: (t.bpm || 120) * 2 }, { toast: true }) },
    { label: "Half  ÷2", run: () => run("tempo_set", { bpm: (t.bpm || 120) / 2 }, { toast: true }) },
    { label: "Faster  +0.5", run: () => run("tempo_nudge", { bpm: 0.5 }) },
    { label: "Slower  −0.5", run: () => run("tempo_nudge", { bpm: -0.5 }) },
    "-",
    { label: (t.follow ? "✓ " : "") + "Speed master follows the tempo", hint: "120 BPM = 1×", run: () => run("tempo_set", { follow: !t.follow }, { toast: true }) },
    { label: (t.prodj ? "✓ " : "") + "Listen to the CDJs (Pro DJ Link)", hint: "tempo + the bar from the decks", run: () => run("tempo_prodj", { state: !t.prodj }, { toast: true }) },
    { label: "MIDI clock", hint: "from the desk's MIDI, or Settings → MIDI on this device", disabled: true },
    "-",
    { label: "Sound: listen to the room…", hint: "the music plays the lights", run: () => import("./sounddialog.js").then((m) => m.openSoundDialog()) },
    { label: "Autopilot…", hint: "a cue list plays itself on the phrase", run: () => import("./autopilot.js").then((m) => m.openAutopilot()) },
  ]);
}

export function initTempo() {
  const box = $("#tempo");
  $("#tempo-tap").addEventListener("pointerdown", (e) => { e.preventDefault(); tap(); });
  $("#tempo-more").addEventListener("click", (e) => openMenu(e.currentTarget));
  box.hidden = false;
  on("snapshot", sync);
  on("lite", sync);
  sync();
  cancelAnimationFrame(raf);
  tick();
}
