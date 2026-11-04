// Talking with the copilot, live: you speak, it answers out loud, and you
// can talk over it.  Gemini's Live model hears and speaks; whatever you ask
// to happen it hands to the desk's own copilot (the same one as typing - the
// room's rules, one Ctrl+Z), then says what the desk did.
//
// The browser gets a pass for one talk from the desk (POST assistant
// {live_voice}); the key never leaves the desk.  Online AI only.
import { post } from "./api.js";

const IN_RATE = 16000, OUT_RATE = 24000;

function b64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
function unb64(s) {
  const bin = atob(s), out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
// the microphone's float samples, down to 16 kHz 16-bit
function pcm16(input, fromRate) {
  const step = fromRate / IN_RATE, n = Math.floor(input.length / step), out = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const v = Math.max(-1, Math.min(1, input[Math.floor(i * step)]));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return new Uint8Array(out.buffer);
}

/** Start a live talk.  `on`: {state(text), heard(text), said(text), did(summary), error(text)}.
 *  Returns stop(). */
export async function startLive(on = {}) {
  const tell = (k, v) => { try { on[k] && on[k](v); } catch (e) { /* the screen's own trouble */ } };
  tell("state", "Connecting…");
  const pass = await post("/api/console/assistant", { live_voice: true });
  if (!pass || !pass.ok) throw new Error((pass && pass.error) || "no pass for talking");
  const ws = new WebSocket(pass.ws);
  let mic = null, inCtx = null, outCtx = null, proc = null, playAt = 0, closed = false;
  const playing = new Set();
  let heard = "", said = "";

  const stop = () => {
    if (closed) return;
    closed = true;
    try { ws.close(); } catch (e) { /* gone */ }
    try { proc && proc.disconnect(); } catch (e) { /* gone */ }
    try { mic && mic.getTracks().forEach((t) => t.stop()); } catch (e) { /* gone */ }
    try { inCtx && inCtx.close(); } catch (e) { /* gone */ }
    try { outCtx && outCtx.close(); } catch (e) { /* gone */ }
    tell("state", "");
  };
  const quiet = () => { for (const s of playing) { try { s.stop(); } catch (e) { /* done */ } } playing.clear(); playAt = 0; };
  const play = (bytes) => {
    if (!outCtx) outCtx = new AudioContext({ sampleRate: OUT_RATE });
    const n = bytes.length >> 1, view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const buf = outCtx.createBuffer(1, n, OUT_RATE), ch = buf.getChannelData(0);
    for (let i = 0; i < n; i++) ch[i] = view.getInt16(i * 2, true) / 0x8000;
    const src = outCtx.createBufferSource();
    src.buffer = buf; src.connect(outCtx.destination);
    playAt = Math.max(playAt, outCtx.currentTime);
    src.start(playAt); playAt += buf.duration;
    playing.add(src); src.onended = () => playing.delete(src);
  };
  const listen = async () => {
    mic = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    inCtx = new AudioContext();
    const srcNode = inCtx.createMediaStreamSource(mic);
    proc = inCtx.createScriptProcessor(4096, 1, 1);
    proc.onaudioprocess = (e) => {
      if (ws.readyState !== 1) return;
      ws.send(JSON.stringify({ realtimeInput: { audio: { data: b64(pcm16(e.inputBuffer.getChannelData(0), inCtx.sampleRate)), mimeType: `audio/pcm;rate=${IN_RATE}` } } }));
    };
    srcNode.connect(proc); proc.connect(inCtx.destination);
    tell("state", "Listening - just talk");
  };
  // what it asks the desk to do: the copilot does it, the answer goes back
  const deskDoes = async (call) => {
    const req = String((call.args || {}).request || "").slice(0, 600);
    tell("state", "The desk is on it…");
    let result;
    try {
      const r = await post("/api/console/assistant", { message: req, session: "voice", preview: false });
      result = r.ok ? { done: r.reply || "done", steps: (r.steps || []).map((s) => s.summary).filter(Boolean).slice(0, 8) }
        : { error: r.error || "it couldn't" };
      tell("did", r.ok ? (r.reply || "Done.") : `Couldn't: ${r.error || "no answer"}`);
    } catch (e) { result = { error: e.message }; }
    if (ws.readyState === 1) {
      ws.send(JSON.stringify({ toolResponse: { functionResponses: [{ id: call.id, name: call.name, response: { result } }] } }));
    }
    tell("state", "Listening - just talk");
  };

  ws.onopen = () => ws.send(JSON.stringify(pass.setup));
  ws.onerror = () => { tell("error", "the talk was cut off - check the internet"); stop(); };
  ws.onclose = (e) => { if (!closed) { tell("error", e.reason ? `the talk ended: ${e.reason}` : "the talk ended"); stop(); } };
  ws.onmessage = async (ev) => {
    let m;
    try { m = JSON.parse(typeof ev.data === "string" ? ev.data : await ev.data.text()); } catch (e) { return; }
    if (m.setupComplete) { listen().catch((e) => { tell("error", `the microphone: ${e.message}`); stop(); }); return; }
    const sc = m.serverContent;
    if (sc) {
      if (sc.interrupted) quiet();                       // (you talked over it: it stops)
      if (sc.inputTranscription && sc.inputTranscription.text) heard += sc.inputTranscription.text;
      if (sc.outputTranscription && sc.outputTranscription.text) said += sc.outputTranscription.text;
      for (const part of (sc.modelTurn && sc.modelTurn.parts) || []) {
        if (part.inlineData && /audio\/pcm/.test(part.inlineData.mimeType || "")) play(unb64(part.inlineData.data));
      }
      if (sc.turnComplete || sc.generationComplete) {
        if (heard.trim()) tell("heard", heard.trim());
        if (said.trim()) tell("said", said.trim());
        heard = ""; said = "";
      }
    }
    if (m.toolCall) for (const c of m.toolCall.functionCalls || []) if (c.name === "do_on_desk") await deskDoes(c);
    if (m.goAway) tell("state", "The talk ends soon - start it again to go on");
  };
  return stop;
}
