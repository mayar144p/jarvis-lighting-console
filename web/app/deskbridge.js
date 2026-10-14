// The desktop app (Electron) asks its questions through the page, so they
// look like the rest of the desk.  In a normal browser this does nothing.
import { choiceBox } from "./ui.js";

export function initDeskBridge() {
  const desk = window.jarvisDesk;
  if (!desk) return;
  desk.onAsk(async (q) => {
    const i = await choiceBox(q.title || "Jarvis", q);
    desk.answer(q.id, i);
  });
  desk.ready();
}
