// Dialogs about one light: movement-speed calibration, a light test,
// requesting a fixture, and a fixture made by hand.

import { get, post } from "./api.js";
import { patch, outputState } from "./store.js";
import { run } from "./actions.js";
import { h, modal, toast, confirmBox } from "./ui.js";
import { openChannels } from "./dialogs.js";

// ===================================================== movement speed
// Time the REAL light, so the visualiser moves it at the same pace: the
// light sweeps pan (then tilt) end to end at top speed and you tap when
// it stops.  Saved per fixture model, so every head of it matches.
const REACTION_S = 0.2;          // a tap lands about this late
export async function openMotionCalibration(hd) {
  const head = hd.head_no;
  const got = await run("motion_get", { head }, { silentError: true });
  if (!got.ok) { toast(got.error || "This fixture cannot be calibrated", "bad"); return; }
  const axes = got.axes || [];
  if (!axes.length) { toast("This fixture has no pan or tilt", "bad"); return; }
  const taps = { pan: [], tilt: [] };
  const inputs = {};
  let t0 = 0;
  let axis = axes[0];
  let phase = "idle";                       // idle -> ready -> timing
  const live = outputState() === "live";
  const stepText = h("div.cal-step");
  const big = h("button.btn.primary.cal-big");
  const result = h("div.muted.small");
  const fields = h("div.form-grid", ...axes.map((ax) => {
    inputs[ax] = h("input", { type: "number", min: 0.2, max: 60, step: 0.1,
      value: got.motion && got.motion[ax + "_s"] ? got.motion[ax + "_s"] : "", placeholder: "default" });
    return h("label.field", h("span", `Full ${ax}, seconds`), inputs[ax]);
  }));
  const axisPick = h("div.row-btns", ...axes.map((ax) => h("button.btn.small", {
    dataset: { ax }, onclick: () => { axis = ax; reset(); },
  }, `Time ${ax}`)));

  const avg = (list) => list.reduce((a, b) => a + b, 0) / list.length;
  function render() {
    [...axisPick.children].forEach((b) => b.classList.toggle("on", b.dataset.ax === axis));
    if (phase === "idle") {
      stepText.textContent = `1. Send the head to the start of its ${axis}. Wait until the REAL light has stopped moving.`;
      big.textContent = `Move to ${axis} start`;
    } else if (phase === "ready") {
      stepText.textContent = `2. Press Go and watch the real light: it sweeps the whole ${axis} at top speed.`;
      big.textContent = "Go";
    } else {
      stepText.textContent = "3. Tap the moment the real light STOPS.";
      big.textContent = "It stopped!";
    }
    const n = taps[axis].length;
    result.textContent = n ? `${axis}: ${taps[axis].map((x) => x.toFixed(2)).join(" s, ")} s → using ${avg(taps[axis]).toFixed(2)} s` +
      (n < 2 ? " (time it twice for a better average)" : "") : "";
  }
  function reset() { phase = "idle"; render(); }
  big.addEventListener("click", async () => {
    if (phase === "idle") {
      const r = await run("motion_test", { head, axis, to: "start" });
      if (r.ok) { phase = "ready"; render(); }
    } else if (phase === "ready") {
      phase = "timing";
      render();
      t0 = performance.now();
      const r = await run("motion_test", { head, axis, to: "end" });
      if (!r.ok) reset();
    } else {
      const s = Math.max(0.2, (performance.now() - t0) / 1000 - REACTION_S);
      taps[axis].push(s);
      inputs[axis].value = avg(taps[axis]).toFixed(2);
      reset();
    }
  });
  const body = h("div.cal",
    h("p", `Make the 3D view move #${head} ${hd.name || hd.model} as fast as the real light. The result is saved for every ${hd.model}.`),
    live ? null : h("p.out-bad", "The output is not live, so the real light will not move. Press Go live first, or type the times below from a stopwatch."),
    axisPick, stepText, big, result, fields,
    h("p.muted.small", "Tip: time each axis twice. The speed channel is set to fastest for the test, and the head gets back exactly what it was doing when you close this."));
  const close = modal({
    title: "Calibrate movement speed", body,
    foot: [
      h("button.btn", { onclick: async () => { await run("motion_set", { head, clear: true }, { toast: true }); close(); } }, "Back to defaults"),
      h("span.grow"),
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", { onclick: async () => {
        const params = { head };
        for (const ax of axes) if (inputs[ax].value) params[ax + "_s"] = +inputs[ax].value;
        const r = await run("motion_set", params, { toast: true });
        if (r.ok) close();
      } }, "Save"),
    ],
    onClose: () => run("motion_test_end", { head }, { silentError: true }),
  });
  render();
}

// ================================================= fixture from a manual
// Any light or effect no library has: paste the DMX chart or drop the
// manual's PDF, check the table, save.  Nothing is stored until Save.
const FIXTURE_FUNCTIONS = ["dimmer", "red", "green", "blue", "white", "amber", "uv", "cyan", "magenta", "yellow",
  "pan", "pan fine", "tilt", "tilt fine", "pan/tilt speed", "shutter", "strobe", "colour wheel", "colour macro",
  "gobo wheel", "gobo rotation", "prism", "zoom", "focus", "frost", "iris",
  "fx fire", "fx arm", "fx fan", "fog output", "fx height", "fx mode",
  "laser output", "laser pattern", "laser size", "laser rotation", "laser x", "laser y", "laser speed", "laser colour", "laser beam",
  "setting", "unused"];
const FIXTURE_TYPES = [["light", "Light"], ["laser", "Laser"], ["confetti", "Confetti"], ["co2", "CO2 jet"],
  ["flame", "Flame"], ["spark", "Spark fountain"], ["fog", "Fog"], ["haze", "Haze"], ["bubble", "Bubbles"],
  ["snow", "Snow"], ["other", "Other effect"]];

async function pdfText(file) {
  const pdfjs = await import("/vendor/pdfjs/pdf.min.mjs");
  pdfjs.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.min.mjs";
  const doc = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise;
  const pages = [];
  for (let i = 1; i <= Math.min(doc.numPages, 60); i++) {
    const page = await doc.getPage(i);
    const tc = await page.getTextContent();
    // rebuild lines from the text runs' y positions
    const rows = new Map();
    for (const it of tc.items) {
      const y = Math.round(it.transform[5]);
      rows.set(y, (rows.get(y) || "") + (rows.has(y) ? " " : "") + it.str);
    }
    pages.push([...rows.entries()].sort((a, b) => b[0] - a[0]).map(([, t]) => t).join("\n"));
  }
  return pages.join("\n");
}

/** "Request a fixture": a light no library has yet.  Fills in the desk's
 *  GitHub request form (brand, model, mode, the manual's link) and opens
 *  it - pressing Submit there needs a GitHub account. */
export function requestFixture(q = "") {
  const words = String(q || "").trim().split(/\s+/).filter(Boolean);
  const maker = h("input", { type: "text", placeholder: "Chauvet", value: words[0] || "" });
  const model = h("input", { type: "text", placeholder: "Intimidator Wave 360 IRC", value: words.slice(1).join(" ") });
  const mode = h("input", { type: "text", placeholder: "14-channel" });
  const link = h("input", { type: "url", placeholder: "https://... (the manual or DMX chart)", style: { width: "100%" } });
  const note = h("textarea", { rows: 3, placeholder: "Anything else: a similar light that almost works, what it's for" });
  const go = async () => {
    if (!maker.value.trim() && !model.value.trim()) { toast("Say which light: its brand and model", "bad"); return; }
    const qs = new URLSearchParams({ maker: maker.value, model: model.value, mode: mode.value, link: link.value, note: note.value });
    const d = await get("/api/fixtures/request?" + qs).catch((e) => ({ error: e.message }));
    if (d.error) { toast(d.error, "bad"); return; }
    window.open(d.url, "_blank", "noopener");
    toast("The request opened in your browser: press Submit there. The manual's PDF can be dragged in.", "ok");
    close();
  };
  const close = modal({
    title: "Request a fixture",
    body: h("div.form-grid",
      h("p.muted.small", { style: { gridColumn: "1 / -1", margin: 0 } },
        "For a light none of the libraries has. This opens a filled-in request on the desk's GitHub page; once it is added, it arrives with the next update. Can't wait? ",
        h("b", "From its manual…"), " adds it yourself now."),
      h("label.field", h("span", "Brand"), maker),
      h("label.field", h("span", "Model"), model),
      h("label.field", h("span", "DMX mode"), mode),
      h("label.field", { style: { gridColumn: "1 / -1" } }, h("span", "Link to the manual"), link),
      h("label.field", { style: { gridColumn: "1 / -1" } }, h("span", "Note"), note)),
    foot: [h("button.btn", { onclick: () => close() }, "Cancel"), h("button.btn.primary", { onclick: go }, "Open the request")],
  });
  setTimeout(() => (maker.value ? model : maker).focus(), 50);
}


export function openManualFixture(onSaved) {
  const maker = h("input", { type: "text", placeholder: "e.g. Chauvet DJ" });
  const model = h("input", { type: "text", placeholder: "e.g. Funfetti Shot" });
  const text = h("textarea", { rows: 8, placeholder: "Paste the DMX chart here (from the manual or its PDF), e.g.\n1 Off/On\n000-009 Off\n010-255 On" });
  const file = h("input", { type: "file", accept: ".pdf,.txt,application/pdf,text/plain" });
  const status = h("div.muted.small");
  const review = h("div.man-review");
  let draft = null;
  file.addEventListener("change", async () => {
    const f = file.files && file.files[0];
    if (!f) return;
    status.textContent = "Reading the file…";
    try {
      text.value = f.name.toLowerCase().endsWith(".pdf") ? await pdfText(f) : await f.text();
      status.textContent = `Read ${f.name}. Press "Read the chart".`;
    } catch (err) {
      status.textContent = "Could not read that file: " + err.message;
    }
  });
  const readBtn = h("button.btn.primary", {
    onclick: async () => {
      status.textContent = "Reading the DMX chart…";
      const d = await post("/api/fixtures/from_manual", { text: text.value, manufacturer: maker.value, model: model.value }).catch((e) => ({ error: e.message }));
      if (d.error) { status.textContent = d.error; return; }
      draft = d.draft;
      status.textContent = (draft.via === "ai" ? "Read by the AI." : "Read by the offline reader.")
        + " Check every channel against the manual before saving." + ((draft.warnings || []).length ? " " + draft.warnings.join(" ") : "");
      renderReview();
    },
  }, "Read the chart");

  function renderReview() {
    if (!draft || !(draft.modes || []).length) { review.replaceChildren(); return; }
    if (draft.manufacturer && !maker.value) maker.value = draft.manufacturer;
    if (draft.model && !model.value) model.value = draft.model;
    const type = h("select.select", ...FIXTURE_TYPES.map(([k, l]) => h("option", { value: k }, l)));
    type.value = draft.type || "light";
    type.addEventListener("change", () => { draft.type = type.value; });
    const tables = draft.modes.map((m) => {
      const name = h("input", { type: "text", value: m.name, style: { width: "160px" } });
      name.addEventListener("input", () => { m.name = name.value; });
      return h("div.man-mode", h("label.field.inline", h("span", "Mode"), name),
        h("table.chan-table", h("thead", h("tr", h("th", "Ch"), h("th", "Name"), h("th", "Does"), h("th", "Values (one per line: 0-9 Off)"))),
          h("tbody", ...m.channels.map((c, i) => {
            const nm = h("input", { type: "text", value: c.name });
            nm.addEventListener("input", () => { c.name = nm.value; });
            const fn = h("select.select", ...FIXTURE_FUNCTIONS.map((f) => h("option", { value: f }, f)));
            fn.value = c.function;
            fn.addEventListener("change", () => { c.function = fn.value; });
            const rg = h("textarea", { rows: Math.min(4, Math.max(1, c.ranges.length)) });
            rg.value = c.ranges.map(([lo, hi, l]) => `${lo}-${hi} ${l}`).join("\n");
            rg.addEventListener("change", () => {
              c.ranges = rg.value.split("\n").map((ln) => /^\s*(\d{1,3})\s*[-–]\s*(\d{1,3})\s*(.*)$/.exec(ln)).filter(Boolean)
                .map((x) => [+x[1], +x[2], x[3].trim()]);
            });
            return h("tr", h("td.mono", i + 1), h("td", nm), h("td", fn), h("td", rg));
          }))));
    });
    review.replaceChildren(h("div.form-grid", h("label.field", h("span", "Kind of fixture"), type)), ...tables);
  }

  const close = modal({
    title: "Fixture from its manual", wide: true,
    body: h("div.man",
      h("div.form-grid", h("label.field", h("span", "Manufacturer"), maker), h("label.field", h("span", "Model"), model),
        h("label.field", h("span", "Manual (PDF or text)"), file)),
      text, h("div.row-btns", readBtn, status), review),
    foot: [h("span.muted.small.grow", "The desk stores exactly this table. Effects fire only from their armed FX buttons."),
      h("button.btn", { onclick: () => close() }, "Cancel"),
      h("button.btn.primary", {
        onclick: async () => {
          if (!draft) { toast("Read the chart first", "bad"); return; }
          if (!maker.value.trim() || !model.value.trim()) { toast("Give the manufacturer and the model, so you can find it later", "bad"); return; }
          draft.manufacturer = maker.value.trim();
          draft.model = model.value.trim();
          const d = await post("/api/fixtures/from_manual/save", { draft }).catch((e) => ({ error: e.message }));
          if (d.error) { toast(d.error, "bad"); return; }
          toast(d.summary, "ok");
          close();
          if (onSaved) onSaved(d.fixture);
        },
      }, "Save to library")],
  });
}

// A model the desk hasn't seen pass the test: offer it while the real light
// is there to look at (only when the output is live - otherwise it can't
// react).  Most "it doesn't move / wrong colours" turns out to be the mode
// set on the light, and that is the first question.
export async function offerLightTest(headNo) {
  if (!headNo) return;
  await new Promise((r) => setTimeout(r, 500));          // the new patch arrives
  const hd = patch().find((x) => x.head_no === headNo);
  if (!hd || hd.tested) return;
  if (outputState() !== "live") {
    toast(`When the real ${hd.model} is connected: right-click it -> Test this light (30 s: mode, light, move, colour, strobe)`, "", 8000);
    return;
  }
  if (await confirmBox(`Test the new ${hd.model}?`,
    `30 seconds with the real light in front of you: is it in the right mode, does it light, move, change colour and strobe as the desk expects. `
    + `Every ${hd.model} is ready after one test.`, { ok: "Test it now" })) openLightTest(hd);
}

// ====================================================== test this light
// A fixture file can be wrong in ways no code can see: a shutter "open"
// value it never states, a channel order that does not match the light's
// mode.  So a new model gets a 30-second test with the operator watching
// the REAL light: lit? (and if not, find the open value) - moving? - the
// right colours?  A model that passes is not asked about again.
export async function openLightTest(hd) {
  const head = hd.head_no;
  const st = await run("light_test", { head, step: "start" }, { silentError: true });
  if (!st.ok) { toast(st.error || "This light cannot be tested", "bad"); return; }
  const res = { light: null, move: true, colour: true, strobe: true };
  const many = (st.heads || 1) > 1 ? ` (all ${st.heads} heads)` : "";
  const box = h("div.lt");
  const live = outputState() === "live";
  let closed = false;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const ask = (text, buttons) => new Promise((resolve) => {
    box.replaceChildren(
      live ? null : h("p.out-bad", "The output is not live: the real light will not react. Press Go live first."),
      h("p.lt-q", text),
      h("div.row-btns", ...buttons.map(([label, value, cls]) => h("button.btn" + (cls ? "." + cls : ""), { onclick: () => resolve(value) }, label))));
  });

  // Every DMX channel of the light on its own fader, written straight to
  // the wire: finds what the REAL light needs even when the file (or the
  // mode it describes) is wrong.  "It's on" keeps what was found.
  function faders() {
    return new Promise((resolve) => {
      const rows = (st.slots || []).map((c) => {
        const input = h("input", { type: "range", min: 0, max: 255, value: c.value });
        const out = h("span.mono.small.lt-v", String(c.value));
        let t = 0;
        input.addEventListener("input", () => {
          out.textContent = input.value;
          clearTimeout(t);
          t = setTimeout(() => run("light_test", { head, step: "raw", slot: c.n, value: +input.value }, { silentError: true }), 40);
        });
        const quick = (v) => h("button.chip", { onclick: () => { input.value = v; input.dispatchEvent(new Event("input")); } }, String(v));
        return h("div.lt-ch",
          h("span.lt-n", { title: `DMX address ${c.abs}` }, `${c.n}`),
          h("span.lt-l", { title: `${c.label} (${c.role}) - DMX ${c.abs}` }, c.label),
          input, out, h("span.lt-q2", quick(0), quick(128), quick(255)));
      });
      box.replaceChildren(
        live ? null : h("p.out-bad", "The output is not live: the real light will not react. Press Go live first."),
        h("p.small", "Each fader drives one DMX channel of this light directly (the number is its channel, hover for the address). "
          + "Move them until the real light comes on - usually a dimmer to 255 and a shutter somewhere between 0 and 255. "
          + "If the channel that lights it is not the one called Dimmer or Shutter, the light is in a different DMX mode from the one added."),
        h("div.lt-chs", ...rows),
        h("div.row-btns",
          h("button.btn.primary", { onclick: async () => {
            const r = await run("light_test", { head, step: "keep" }, { silentError: true });
            if (r.ok) toast(r.summary, "ok");
            resolve(true);
          } }, "It's on - keep these"),
          h("button.btn", { onclick: () => resolve(false) }, "Still dark")));
    });
  }

  async function lightStep() {
    const first = await ask(`#${head} ${hd.name || hd.model} should now be ON: full, white, centred. Is the real light on?`,
      [["Yes, it's on", "yes", "primary"], ["No, it's dark", "no"], ["Set channels by hand", "hand"]]);
    if (first === "yes") return true;
    if (first === "hand") return faders();
    const cands = st.gate ? st.candidates || [] : [];
    let stopped = false;
    for (let i = 0; i < cands.length && !closed; i++) {
      await run("light_test", { head, step: "open", value: cands[i] }, { silentError: true });
      const a = await ask(`Trying shutter value ${cands[i]} (${i + 1} of ${cands.length}). Is the real light on now?`,
        [["It's on!", "on", "primary"], ["Still dark", "next"], ["Set channels by hand", "stop"]]);
      if (a === "on") {
        await run("remember_open", { head, value: cands[i] }, { toast: true });
        return true;
      }
      if (a === "stop") { stopped = true; break; }
    }
    if (st.gate) await run("light_test", { head, step: "open", value: st.open }, { silentError: true });
    // still dark: the file may not have named the channel that opens it
    // (a lamp / "control" channel, or a mode whose channels sit elsewhere)
    const hunt = st.hunt || [];
    if (!stopped && hunt.length && !closed && await ask("None of the shutter values lit it. Try each of its other channels in turn? (Some lights need a control channel set before they light.)",
      [["Try them", true, "primary"], ["Set channels by hand", false]])) {
      for (let i = 0; i < hunt.length && !closed; i++) {
        const c = hunt[i];
        await run("light_test", { head, step: "channel", role: c.role, value: c.value }, { silentError: true });
        const a = await ask(`Trying ${c.label} at ${c.value} (${i + 1} of ${hunt.length}). Is the real light on now?`,
          [["It's on!", "on", "primary"], ["Still dark", "next"], ["Set channels by hand", "stop"]]);
        if (a === "on") {
          await run("remember_open", { head, role: c.role, value: c.value }, { toast: true });
          return true;
        }
        if (a === "stop") break;
      }
      await run("light_test", { head, step: "channel" }, { silentError: true });
    }
    if (closed) return false;
    return faders();
  }

  // The desk can't see what is set on the light itself: the mode (how many
  // channels it listens to) and its address.  A mismatch makes some heads
  // not move and colours land wrong - so it's the first question.
  async function modeStep() {
    const modes = st.modes || [];
    const a = await ask(`Look at the light's own display (or its DIP switches). It should be in mode "${st.mode}" - ${st.channels} channels - `
      + `starting at DMX address ${String(st.address).split(".").pop()}${st.address.includes(".") ? ` on universe ${st.address.split(".")[0]}` : ""}. Is it?`,
      [["Yes, it matches", "yes", "primary"], ...(modes.length > 1 ? [["It shows another mode", "other"]] : []), ["I can't see it", "skip"]]);
    if (a !== "other") return false;
    const pick = await ask("Which mode does the light show?",
      [...modes.filter((m) => m.name !== st.mode).map((m) => [`${m.name} (${m.channels} ch)`, m.name, "primary"]), ["Back", ""]]);
    if (!pick) return modeStep();
    // every light of this model in the old mode: they're set the same way
    const same = patch().filter((x) => x.model === hd.model && x.manufacturer === hd.manufacturer && x.mode === st.mode).map((x) => x.head_no);
    const r = await run("change_type", { heads: same, fixture_id: st.fixture_id, mode: pick }, { silentError: true });
    if (!r.ok) { toast(r.error || "Couldn't change the mode", "bad"); return false; }
    toast(r.summary + (/moved/.test(r.summary || "") ? " - set those lights' addresses to match" : ""), "ok", 9000);
    return true;
  }

  async function strobeStep() {
    await run("light_test", { head, step: "strobe", value: "fast" }, { silentError: true });
    const a = await ask(`It should be strobing FAST now${many}. Is it?`, [["Yes", true, "primary"], ["No", false]]);
    await run("light_test", { head, step: "strobe", value: "slow" }, { silentError: true });
    const b = a && await ask("Now it should strobe SLOWLY. Is it?", [["Yes", true, "primary"], ["No", false]]);
    await run("light_test", { head, step: "strobe", value: "off" }, { silentError: true });
    return !!(a && b);
  }

  async function sweep(axis) {
    await run("light_test", { head, step: axis, value: 0.3 }, { silentError: true });
    await wait(1200);
    await run("light_test", { head, step: axis, value: 0.7 }, { silentError: true });
    // "all 4 heads" only for an axis each head has (a Wave 360's pan turns the whole body)
    const n = (hd.map || []).filter((r) => r === axis).length;
    const a = await ask(`It should ${axis === "pan" ? "turn left and right (pan)" : "tip down and up (tilt)"} now${n > 1 ? ` (all ${n} heads)` : ""}. Did the real light do that?`,
      [["Yes", "yes", "primary"], ["Again", "again"], ["No / something else moved", "no"]]);
    if (a === "again") return sweep(axis);
    await run("light_test", { head, step: axis, value: 0.5 }, { silentError: true });
    return a === "yes";
  }

  async function colours() {
    for (const [hex, name] of [["#ff0000", "red"], ["#00ff00", "green"], ["#0000ff", "blue"]]) {
      await run("light_test", { head, step: "colour", hex }, { silentError: true });
      const a = await ask(`It should be ${name.toUpperCase()} now${many}${st.mixing ? "" : " (or the closest colour on its wheel)"}. Is it?`,
        [["Yes", true, "primary"], ["No", false]]);
      if (!a) return false;
    }
    await run("light_test", { head, step: "colour", hex: "#ffffff" }, { silentError: true });
    return true;
  }

  const close = modal({
    title: `Test ${hd.model}`, body: box, wide: true,
    foot: [h("span.muted.small.grow", `Mode ${st.mode} (${st.channels} channels) at ${st.address}. The light gets back what it was doing afterwards.`),
      h("button.btn", { onclick: () => close() }, "Close")],
    onClose: () => { closed = true; run("light_test", { head, step: "end" }, { silentError: true }); },
  });

  if (await modeStep()) {
    // the light is re-patched in the mode it really is in: test that
    close();
    const again = patch().find((x) => x.head_no === head);
    if (again) openLightTest(again);
    return;
  }
  if (closed) return;
  res.light = await lightStep();
  if (!closed && res.light && st.pan) res.move = await sweep("pan");
  if (!closed && res.light && st.tilt && res.move) res.move = await sweep("tilt");
  if (!closed && res.light && st.colour) res.colour = await colours();
  if (!closed && res.light && st.strobe) res.strobe = await strobeStep();
  if (closed) return;
  const r = await run("light_tested", { head, light: !!res.light, move: res.move, colour: res.colour, strobe: res.strobe }, { silentError: true });
  const ok = r.ok && r.tested;
  box.replaceChildren(
    h(ok ? "p.out-ok" : "p.out-bad", ok ? `✓ ${hd.model} passed: it lights, moves, changes colour${st.strobe ? " and strobes" : ""} as expected. Every ${hd.model} is ready.`
      : `⚠ ${hd.model} needs attention.`),
    ...((r.advice || []).map((t) => h("p.small", t))),
    ok ? null : h("div.row-btns", h("button.btn", { onclick: () => { close(); openChannels([head]); } }, "Show its DMX channels")));
}
