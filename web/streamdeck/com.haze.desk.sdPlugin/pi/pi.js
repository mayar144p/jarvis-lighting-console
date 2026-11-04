// Settings pages for the Haze keys.  Fields with data-key are this key's
// settings; fields with data-global are shared by every key (where the desk is).
"use strict";
let socket = null, uuid = null;
const send = (obj) => socket && socket.readyState === 1 && socket.send(JSON.stringify(obj));

function fill(sel, values) {
  document.querySelectorAll(sel).forEach((el) => {
    const k = el.dataset.key || el.dataset.global;
    if (values && values[k] !== undefined) el.value = values[k];
  });
}

function collect(attr) {
  const out = {};
  document.querySelectorAll(`[data-${attr}]`).forEach((el) => {
    const k = el.dataset[attr];
    out[k] = el.type === "number" ? (el.value === "" ? "" : Number(el.value)) : el.value.trim();
  });
  return out;
}

// eslint-disable-next-line no-unused-vars
function connectElgatoStreamDeckSocket(port, pluginUUID, registerEvent, info, actionInfo) {
  uuid = pluginUUID;
  const action = JSON.parse(actionInfo || "{}");
  fill("[data-key]", (action.payload || {}).settings || {});
  socket = new WebSocket("ws://127.0.0.1:" + port);
  socket.onopen = () => {
    send({ event: registerEvent, uuid });
    send({ event: "getGlobalSettings", context: uuid });
  };
  socket.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.event === "didReceiveGlobalSettings") {
      const g = (msg.payload || {}).settings || {};
      fill("[data-global]", { host: "127.0.0.1", port: 8000, ...g });
    }
  };
  document.querySelectorAll("[data-key]").forEach((el) =>
    el.addEventListener("change", () => send({ event: "setSettings", context: uuid, payload: collect("key") })));
  document.querySelectorAll("[data-global]").forEach((el) =>
    el.addEventListener("change", () => send({ event: "setGlobalSettings", context: uuid, payload: collect("global") })));
}
