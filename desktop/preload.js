// The desk page shows the app's questions in its own boxes, and a show file
// is saved where the operator picks.  Only these calls cross over; the page
// gets nothing else from Electron.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("jarvisDesk", {
  ready: () => ipcRenderer.send("desk:ready"),
  onAsk: (fn) => ipcRenderer.on("desk:ask", (_e, q) => fn(q)),
  answer: (id, index) => ipcRenderer.send("desk:answer", id, index),
  // a Save as box, then the bytes written there: the path, or null (cancelled)
  saveAs: (name, bytes) => ipcRenderer.invoke("desk:saveAs", name, bytes),
});
