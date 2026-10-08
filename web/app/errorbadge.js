// A warning badge in the top bar the moment the desk hits a problem: an
// error in the page, a desk error, a request that got no answer.  They used
// to be collected quietly for a bug report and nobody knew until a
// screenshot.  Click: what happened, Report it (attached), or Dismiss.
import { $, h, menu } from "./ui.js";
import { noteError, recentErrors, clearErrors, onErrors } from "./api.js";
import { openBugReport } from "./bugreport.js";

// noise that isn't the desk's (the browser's own resize loop, extensions)
const NOISE = /ResizeObserver loop|chrome-extension:|moz-extension:|Script error\.?$/i;

function note(text, where) {
  const t = String(text || "");
  if (!t || NOISE.test(t) || NOISE.test(String(where || ""))) return;
  noteError("page: " + t);
}

function paint() {
  const b = $("#err-btn");
  if (!b) return;
  const n = recentErrors().length;
  b.hidden = !n;
  $("#err-count").textContent = String(n);
  b.title = n ? `${n} problem(s) since you looked - click to see them, report or dismiss` : "";
}

export function initErrorBadge() {
  window.addEventListener("error", (e) => note(e.message, e.filename));
  window.addEventListener("unhandledrejection", (e) => note((e.reason && e.reason.message) || e.reason));
  onErrors(paint);
  const b = $("#err-btn");
  if (!b) return;
  b.addEventListener("click", () => {
    const list = recentErrors().slice(-8).reverse();
    menu(b, [
      ...list.map((x) => ({
        label: x.text.length > 70 ? x.text.slice(0, 70) + "…" : x.text,
        hint: new Date(x.t).toLocaleTimeString(), disabled: true, run: () => {},
      })),
      "-",
      { label: "Report it…", hint: "these are attached", run: () => openBugReport() },
      { label: "Dismiss", run: () => clearErrors() },
    ]);
  });
  paint();
}
