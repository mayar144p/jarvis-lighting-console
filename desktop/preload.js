// The desk page shows the app's questions in its own boxes, and a show file
// is saved where the operator picks.  Only these calls cross over; the page
// gets nothing else from Electron.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("hazeDesk", {
  ready: () => ipcRenderer.send("desk:ready"),
  onAsk: (fn) => ipcRenderer.on("desk:ask", (_e, q) => fn(q)),
  answer: (id, index) => ipcRenderer.send("desk:answer", id, index),
  // a Save as box, then the bytes written there: the path, or null (cancelled)
  saveAs: (name, bytes) => ipcRenderer.invoke("desk:saveAs", name, bytes),
  // no system title bar: the page draws minimise / maximise / close and the
  // Desk menu itself (wincontrols.js)
  win: {
    platform: process.platform,
    act: (what) => ipcRenderer.send("win:do", String(what)),
    onState: (fn) => ipcRenderer.on("win:state", (_e, s) => fn(s)),
    menu: () => ipcRenderer.invoke("win:menu"),
    pick: (id) => ipcRenderer.send("win:run", String(id)),
  },
});
