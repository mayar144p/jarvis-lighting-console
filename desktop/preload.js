// The desk page shows the app's questions in its own boxes.  Only these
// three calls cross over; the page gets nothing else from Electron.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("jarvisDesk", {
  ready: () => ipcRenderer.send("desk:ready"),
  onAsk: (fn) => ipcRenderer.on("desk:ask", (_e, q) => fn(q)),
  answer: (id, index) => ipcRenderer.send("desk:answer", id, index),
});
