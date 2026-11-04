// The desktop app's windows have no system title bar: the desk's own top
// bar is the bar.  Drag it to move the window (double-click maximises);
// minimise, maximise and close sit at its right end, in the desk's look;
// the logo opens the Desk menu (Show mode, the 3D window, the touch locks,
// close).  On a Mac the three window dots stay, over the bar's left corner.
// The 3D window (?window=stage) has the same at the end of its toolbar.
// In a browser none of this shows: the browser has its own.
import { $, h, menu } from "./ui.js";

const ICONS = {
  min: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 6.5h8"/></svg>',
  max: '<svg viewBox="0 0 12 12" aria-hidden="true"><rect x="2" y="2" width="8" height="8" rx="1"/></svg>',
  restore: '<svg viewBox="0 0 12 12" aria-hidden="true"><rect x="2" y="4" width="6" height="6" rx="1"/><path d="M4 4V2.5h5.5V8H8"/></svg>',
  close: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7"/></svg>',
  menu: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 3.5h8M2 6h8M2 8.5h8"/></svg>',
};

export async function openDeskMenu(anchor) {
  const w = window.hazeDesk && window.hazeDesk.win;
  if (!w) return;
  const items = await w.menu();
  menu(anchor, items.map((x) => (x === "-" ? "-" : {
    label: (x.checked === true ? "✓ " : "") + x.label, hint: x.keys || undefined, run: () => w.pick(x.id),
  })));
}

export function initWinControls() {
  const w = window.hazeDesk && window.hazeDesk.win;
  if (!w) return;
  const root = document.documentElement;
  root.classList.add("frameless");
  if (w.platform === "darwin") root.classList.add("mac");
  const bars = [root.dataset.window === "stage" ? $("#vis-bar") : $(".topbar")].filter(Boolean);
  const maxBtns = [];
  for (const bar of bars) {
    // the logo (or, in the 3D window, a menu button) opens the Desk menu
    const brand = bar.querySelector(".brand");
    if (brand) {
      brand.setAttribute("role", "button");
      brand.tabIndex = 0;
      brand.title = "Desk: the windows, Show mode, close";
      brand.classList.add("desk-menu-btn");
      brand.addEventListener("click", () => openDeskMenu(brand));
      brand.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDeskMenu(brand); } });
    }
    if (w.platform === "darwin") continue;             // the Mac's own dots
    const btn = (what, label, icon) => {
      const b = h("button.win-btn." + what, { type: "button", title: label, "aria-label": label, onclick: () => w.act(what) });
      b.innerHTML = icon;
      return b;
    };
    const max = btn("max", "Maximise", ICONS.max);
    maxBtns.push(max);
    const extra = [];
    if (!brand) {
      const m = h("button.win-btn.deskmenu", { type: "button", title: "Desk: the windows, Show mode, close", "aria-label": "Desk menu",
        onclick: (e) => openDeskMenu(e.currentTarget) });
      m.innerHTML = ICONS.menu;
      extra.push(m);
    }
    bar.append(h("div.win-ctl", { role: "group", "aria-label": "Window" }, ...extra,
      btn("min", "Minimise", ICONS.min), max, btn("close", "Close", ICONS.close)));
  }
  w.onState((s) => {
    root.classList.toggle("win-max", !!s.max);
    root.classList.toggle("win-full", !!s.full);
    for (const b of maxBtns) {
      b.innerHTML = s.max || s.full ? ICONS.restore : ICONS.max;
      b.title = s.full ? "Leave full screen" : s.max ? "Restore" : "Maximise";
      b.setAttribute("aria-label", b.title);
    }
  });
}
