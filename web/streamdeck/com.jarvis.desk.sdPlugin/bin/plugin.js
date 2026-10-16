// Jarvis on a Stream Deck: keys and dials send OSC to the desk (Settings ->
// MIDI & OSC -> OSC in, port 8000 by default).  No packages: the Stream
// Deck app talks to plugins over a WebSocket, and this file carries the
// small client it needs, plus the OSC encoder.
"use strict";
const crypto = require("node:crypto");
const dgram = require("node:dgram");
const http = require("node:http");

// ------------------------------------------------------------------ OSC
const pad4 = (b) => Buffer.concat([b, Buffer.alloc((4 - (b.length % 4)) || 4)]);
const oscStr = (s) => pad4(Buffer.from(String(s), "utf8").subarray(0, 255));

/** One OSC message: address + int / float / string arguments. */
function oscMessage(address, ...args) {
  let tags = ",";
  const parts = [];
  for (const a of args) {
    if (typeof a === "number" && Number.isInteger(a)) {
      tags += "i";
      const b = Buffer.alloc(4); b.writeInt32BE(a); parts.push(b);
    } else if (typeof a === "number") {
      tags += "f";
      const b = Buffer.alloc(4); b.writeFloatBE(a); parts.push(b);
    } else {
      tags += "s"; parts.push(oscStr(a));
    }
  }
  return Buffer.concat([oscStr(address), oscStr(tags), ...parts]);
}

/** What a key or dial sends, from the action and its settings. */
function messagesFor(action, event, settings, state = {}) {
  const pb = parseInt(settings.playback, 10);
  const withPb = (addr) => (pb > 0 ? oscMessage(addr, pb) : oscMessage(addr));
  switch (action) {
    case "com.jarvis.desk.go": return event === "keyDown" ? [withPb("/jarvis/go")] : [];
    case "com.jarvis.desk.back": return event === "keyDown" ? [withPb("/jarvis/back")] : [];
    case "com.jarvis.desk.blackout": return event === "keyDown" ? [oscMessage("/jarvis/blackout", state.on ? 1 : 0)] : [];
    case "com.jarvis.desk.tap": return event === "keyDown" ? [oscMessage("/jarvis/tap")] : [];
    case "com.jarvis.desk.macro":
      return event === "keyDown" && settings.macro ? [oscMessage("/jarvis/macro", String(settings.macro))] : [];
    case "com.jarvis.desk.button": {
      const page = parseInt(settings.page, 10) || 1, slot = parseInt(settings.slot, 10);
      const id = String(settings.button || (slot > 0 ? `q${page}-${slot}` : "")).trim();
      if (!/^[\w-]{1,24}$/.test(id)) return [];
      if (event === "keyDown") return [oscMessage(`/jarvis/button/${id}`, 1)];
      if (event === "keyUp") return [oscMessage(`/jarvis/button/${id}`, 0)];
      return [];
    }
    // levels go as 0..1: the desk reads a value of 1 or less as a fraction,
    // so a dial at 1% sent as "1" would be full
    case "com.jarvis.desk.master": return "level" in state ? [oscMessage("/jarvis/master", state.level / 100)] : [];
    case "com.jarvis.desk.fader":
      return "level" in state && pb > 0 ? [oscMessage(`/jarvis/playback/${pb}/level`, state.level / 100)] : [];
    default: return [];
  }
}

// ------------------------------------------------------- the plugin logic
class Plugin {
  constructor(send, osc) {
    this.send = send;               // to the Stream Deck app
    this.osc = osc;                 // (buffer) -> to the desk
    this.global = { host: "127.0.0.1", port: 8000 };
    this.contexts = new Map();      // context -> { action, settings, level, on }
  }

  handle(msg) {
    const { event, action, context, payload = {} } = msg;
    if (event === "didReceiveGlobalSettings") {
      Object.assign(this.global, payload.settings || {});
      return;
    }
    if (!context) return;
    const c = this.contexts.get(context) || { action, settings: {}, level: 100, on: false };
    this.contexts.set(context, c);
    if (payload.settings) c.settings = payload.settings;
    if (event === "willDisappear") { this.contexts.delete(context); return; }
    if (event === "willAppear" || event === "didReceiveSettings") {
      if (typeof payload.state === "number") c.on = payload.state === 1;
      this.paint(context, c);
      return;
    }
    let state = {};
    if (action === "com.jarvis.desk.blackout" && event === "keyDown") {
      c.on = !c.on;
      this.send({ event: "setState", context, payload: { state: c.on ? 1 : 0 } });
      state = { on: c.on };
    } else if (event === "dialRotate") {
      const step = Math.max(1, Math.min(10, parseInt(c.settings.step, 10) || 3));
      c.level = Math.max(0, Math.min(100, c.level + (payload.ticks || 0) * step));
      state = { level: c.level };
    } else if (event === "dialDown" || event === "touchTap") {
      c.level = c.level > 0 ? 0 : 100;
      state = { level: c.level };
    }
    const dial = event === "dialRotate" || event === "dialDown" || event === "touchTap";
    for (const buf of messagesFor(action, dial ? "dial" : event, c.settings, state)) this.osc(buf, this.global);
    if (dial) this.paint(context, c);
  }

  paint(context, c) {
    if (c.action === "com.jarvis.desk.master" || c.action === "com.jarvis.desk.fader") {
      const name = c.action === "com.jarvis.desk.master" ? "Grand master" : `Playback ${c.settings.playback || "?"}`;
      this.send({ event: "setFeedback", context, payload: { title: name, value: `${c.level}%`, indicator: { value: c.level } } });
    } else if (c.action === "com.jarvis.desk.button" && c.settings.label) {
      this.send({ event: "setTitle", context, payload: { title: String(c.settings.label) } });
    } else if (c.action === "com.jarvis.desk.macro" && c.settings.macro) {
      this.send({ event: "setTitle", context, payload: { title: String(c.settings.macro) } });
    }
  }
}

// ------------------------------------------- a small WebSocket client (RFC 6455)
function wsConnect(port, onOpen, onMessage) {
  const key = crypto.randomBytes(16).toString("base64");
  const req = http.request({ host: "127.0.0.1", port, headers: {
    Connection: "Upgrade", Upgrade: "websocket", "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": key } });
  req.on("upgrade", (res, socket, head) => {
    const sendText = (text) => {
      const data = Buffer.from(text, "utf8");
      const mask = crypto.randomBytes(4);
      let header;
      if (data.length < 126) header = Buffer.from([0x81, 0x80 | data.length]);
      else if (data.length < 65536) { header = Buffer.alloc(4); header[0] = 0x81; header[1] = 0x80 | 126; header.writeUInt16BE(data.length, 2); }
      else { header = Buffer.alloc(10); header[0] = 0x81; header[1] = 0x80 | 127; header.writeBigUInt64BE(BigInt(data.length), 2); }
      const masked = Buffer.alloc(data.length);
      for (let i = 0; i < data.length; i++) masked[i] = data[i] ^ mask[i % 4];
      socket.write(Buffer.concat([header, mask, masked]));
    };
    let buf = head && head.length ? Buffer.from(head) : Buffer.alloc(0);
    const pump = () => {
      for (;;) {
        if (buf.length < 2) return;
        const op = buf[0] & 0x0f;
        let len = buf[1] & 0x7f, off = 2;
        if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
        else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
        const masked = buf[1] & 0x80;
        const mOff = off;
        if (masked) off += 4;
        if (buf.length < off + len) return;
        let payload = buf.subarray(off, off + len);
        if (masked) { const m = buf.subarray(mOff, mOff + 4); payload = Buffer.from(payload.map((b, i) => b ^ m[i % 4])); }
        buf = buf.subarray(off + len);
        if (op === 0x1) { try { onMessage(JSON.parse(payload.toString("utf8"))); } catch { /* not JSON: ignore */ } }
        else if (op === 0x8) { socket.end(); return; }
        else if (op === 0x9) { socket.write(Buffer.from([0x8a, 0x80, 0, 0, 0, 0])); }   // pong
      }
    };
    socket.on("data", (d) => { buf = Buffer.concat([buf, d]); pump(); });
    socket.on("close", () => process.exit(0));          // the Stream Deck app went away
    onOpen(sendText);
    pump();
  });
  req.on("error", () => process.exit(1));
  req.end();
}

function start(argv) {
  const arg = (name) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : undefined; };
  const port = parseInt(arg("-port"), 10);
  const uuid = arg("-pluginUUID");
  const register = arg("-registerEvent");
  const udp = dgram.createSocket("udp4");
  wsConnect(port, (sendText) => {
    const send = (obj) => sendText(JSON.stringify(obj));
    const plugin = new Plugin(send, (b, g) => udp.send(b, Number(g.port) || 8000, String(g.host || "127.0.0.1")));
    send({ event: register, uuid });
    send({ event: "getGlobalSettings", context: uuid });
    start.plugin = plugin;
  }, (msg) => start.plugin && start.plugin.handle(msg));
}

if (require.main === module) start(process.argv);
module.exports = { oscMessage, messagesFor, Plugin, wsConnect, start };
