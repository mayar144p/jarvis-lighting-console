// The desk page shows the app's questions in its own boxes, and a show file
// is saved where the operator picks.  Only these calls cross over; the page
// gets nothing else from Electron.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("hazeDesk", {
  ready: () => ipcRenderer.send("desk:ready"),
  onAsk: (fn) => ipcRenderer.on("desk:ask", (_e, q) => fn(q)),
  answer: (id, index) => ipcRenderer.send("desk:answer", id, index),
  // the app asks the page to show something ("phoneqr": the phone remote's QR)
  onDo: (fn) => ipcRenderer.on("desk:do", (_e, what) => fn(String(what))),
  // a Save as box, then the bytes written there: the path, or null (cancelled)
  saveAs: (name, bytes) => ipcRenderer.invoke("desk:saveAs", name, bytes),
  // no system title bar: the page draws minimise / maximise / close and the
  // Desk menu itself (wincontrols.js)
  // NDI video over the network (desktop/ndi.js): sources to show, and the
  // 3D view out.  A frame is {name, w, h, data: RGBA} or {name, error}.
  ndi: {
    status: () => ipcRenderer.invoke("ndi:status"),
    sources: () => ipcRenderer.invoke("ndi:sources"),
    watch: (name) => ipcRenderer.send("ndi:watch", String(name)),
    unwatch: (name) => ipcRenderer.send("ndi:unwatch", String(name)),
    onFrame: (fn) => ipcRenderer.on("ndi:frame", (_e, f) => fn(f)),
    setOut: (cfg) => ipcRenderer.invoke("ndi:setOut", cfg),
    onOut: (fn) => ipcRenderer.on("ndi:out", (_e, s) => fn(s)),
    canSend: (yes) => ipcRenderer.send("ndi:canSend", !!yes),
    frame: (w, h, data, fps) => ipcRenderer.send("ndi:frame", w, h, data, fps),
  },
  win: {
    platform: process.platform,
    act: (what) => ipcRenderer.send("win:do", String(what)),
    onState: (fn) => ipcRenderer.on("win:state", (_e, s) => fn(s)),
    menu: () => ipcRenderer.invoke("win:menu"),
    pick: (id) => ipcRenderer.send("win:run", String(id)),
  },
});
