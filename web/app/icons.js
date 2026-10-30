// The desk's key icons, as on a pro desk's keys and soft buttons: Record,
// Update, the timings (Fade in, Fade out, Delay, Fan), Flip, Mirror and the
// rest.  Line drawings in a 16 x 16 box that take the text colour; Record's
// dot is filled (red on the key).  icon("rec") -> an <svg> element.
const P = {
  rec: '<circle cx="8" cy="8" r="4.6" class="fill"/>',
  update: '<path d="M13 8a5 5 0 1 1-1.5-3.6"/><path d="M13 2.5v3h-3"/><circle cx="8" cy="8" r="1.6" class="fill"/>',
  clear: '<path d="M3.5 3.5l9 9M12.5 3.5l-9 9"/>',
  undo: '<path d="M6 4L2.5 7.5 6 11"/><path d="M3 7.5h6.5a4 4 0 0 1 0 8H8"/>',
  fadein: '<path d="M2 13.5h3l6-11h3"/><path d="M2 13.5h12" class="dim"/>',
  fadeout: '<path d="M2 2.5h3l6 11h3"/><path d="M2 13.5h12" class="dim"/>',
  delay: '<circle cx="8" cy="8.5" r="5.5"/><path d="M8 5.5v3.2l2.2 1.4M6.5 1.5h3"/>',
  fan: '<path d="M8 14V3M8 14L3 4M8 14l5-10M8 14L1.5 8M8 14l6.5-6"/>',
  flip: '<path d="M8 1.5v13" class="dim"/><path d="M5.5 4L2 8l3.5 4M10.5 4L14 8l-3.5 4"/>',
  mirror: '<path d="M8 1.5v13" class="dim"/><path d="M6 4H2.5v8H6M10 4h3.5v8H10"/>',
  locate: '<circle cx="8" cy="8" r="4.5"/><path d="M8 1v3M8 12v3M1 8h3M12 8h3"/><circle cx="8" cy="8" r="1.3" class="fill"/>',
  lineup: '<path d="M2 14l4-9 4 9"/><path d="M11 10.5l1.5 1.5L15 9" /><circle cx="6" cy="3.5" r="1.5"/>',
  highlight: '<circle cx="8" cy="8" r="3"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6L13 13M3 13l1.4-1.4M11.6 4.4L13 3"/>',
  prev: '<path d="M10.5 3L5.5 8l5 5"/>',
  next: '<path d="M5.5 3l5 5-5 5"/>',
  all: '<rect x="2" y="2" width="5" height="5" rx="1"/><rect x="9" y="2" width="5" height="5" rx="1"/><rect x="2" y="9" width="5" height="5" rx="1"/><rect x="9" y="9" width="5" height="5" rx="1"/>',
  odd: '<rect x="2" y="2" width="5" height="5" rx="1" class="fill"/><rect x="9" y="2" width="5" height="5" rx="1"/><rect x="2" y="9" width="5" height="5" rx="1"/><rect x="9" y="9" width="5" height="5" rx="1" class="fill"/>',
  even: '<rect x="2" y="2" width="5" height="5" rx="1"/><rect x="9" y="2" width="5" height="5" rx="1" class="fill"/><rect x="2" y="9" width="5" height="5" rx="1" class="fill"/><rect x="9" y="9" width="5" height="5" rx="1"/>',
  none: '<rect x="2" y="2" width="12" height="12" rx="2" class="dim"/><path d="M4.5 4.5l7 7"/>',
  plot: '<rect x="1.5" y="2.5" width="13" height="11" rx="1"/><circle cx="5" cy="6" r="1.2" class="fill"/><circle cx="8" cy="6" r="1.2" class="fill"/><circle cx="11" cy="6" r="1.2" class="fill"/><path d="M3.5 10.5h9"/>',
  cues: '<path d="M2 3.5h2M6 3.5h8M2 8h2M6 8h8M2 12.5h2M6 12.5h8"/>',
  patch: '<path d="M5.5 1.5v4M10.5 1.5v4"/><path d="M3.5 5.5h9v2.5a4.5 4.5 0 0 1-9 0z"/><path d="M8 12.5v2"/>',
  outputs: '<path d="M2 8h2.5l2-4.5 3 9 2-4.5H14"/>',
  macros: '<path d="M3 2.5l5 3-5 3z" class="fill"/><path d="M10 4h4M10 8h4M3 12h11"/>',
  setup: '<circle cx="8" cy="8" r="2.2"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4"/>',
  help: '<circle cx="8" cy="8" r="6.5"/><path d="M6 6.2a2 2 0 1 1 2.8 1.8c-.6.3-.8.7-.8 1.3v.4M8 11.8v.2"/>',
  go: '<path d="M4.5 2.5l9 5.5-9 5.5z" class="fill"/>',
  stop: '<rect x="3.5" y="3.5" width="9" height="9" rx="1" class="fill"/>',
  back: '<path d="M11.5 2.5l-9 5.5 9 5.5z" class="fill"/>',
  flash: '<path d="M9.5 1.5L3.5 9h4l-1 5.5 6-7.5h-4z" class="fill"/>',
  button: '<rect x="2" y="3.5" width="12" height="9" rx="2"/><path d="M5.5 8h5"/>',
  copy: '<rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5"/>',
  enter: '<path d="M13.5 3v5.5H3"/><path d="M6 5.5l-3 3 3 3"/>',
  rubout: '<path d="M5 3.5h9v9H5L1.5 8z"/><path d="M7.5 6l4 4M11.5 6l-4 4"/>',
  caret: '<path d="M4 6.5l4 4 4-4"/>',
  caretr: '<path d="M6.5 4l4 4-4 4"/>',
  full: '<path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4"/>',
  pages: '<path d="M2.5 4h11M2.5 8h11M2.5 12h11"/>',
  music: '<path d="M6 12V3.5l7-1.5v8.5"/><circle cx="4.3" cy="12" r="1.8" class="fill"/><circle cx="11.3" cy="10.5" r="1.8" class="fill"/>',
  camera: '<path d="M2 5.5h3l1.2-2h3.6l1.2 2h3v7.5H2z"/><circle cx="8" cy="9" r="2.3"/>',
  play: '<path d="M4.5 2.5l9 5.5-9 5.5z" class="fill"/>',
};

export function icon(name, cls = "") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 16 16");
  s.setAttribute("aria-hidden", "true");
  s.setAttribute("class", ("ic ic-" + name + " " + cls).trim());
  s.innerHTML = P[name] || "";
  return s;
}

export const ICON_NAMES = Object.keys(P);

// The old text symbols (▾ ▸ ◀ ▶ ⛶ ☰ ♪ 📷) on buttons, chips, labels and
// menus become these line icons wherever they appear - also in text set
// later.  (Never inside a field, a select or the 3D's own drawing.)
const GLYPH = { "▾": "caret", "▸": "caretr", "◀": "prev", "▶": "play", "⛶": "full", "☰": "pages", "♪": "music", "📷": "camera" };
const GLYPH_RE = /▾|▸|◀|▶|⛶|☰|♪|📷/;
const GLYPH_SPLIT = /(▾|▸|◀|▶|⛶|☰|♪|📷)/;
function swapText(node) {
  const p = node.parentElement;
  if (!p || p.closest("select, option, textarea, input, svg, canvas, script, style, [contenteditable], .no-glyph")) return;
  const parts = node.data.split(GLYPH_SPLIT);
  if (parts.length < 2) return;
  const frag = document.createDocumentFragment();
  parts.forEach((s, k) => {
    if (!s) return;
    if (k % 2) frag.append(icon(s === "▶" && /next/i.test(parts[k - 1] || "") ? "next" : GLYPH[s], "glyph"));
    else frag.append(s.replace(/^ (?=$)/, ""));
  });
  node.replaceWith(frag);
}
function sweep(root) {
  if (root.nodeType === 3) { if (GLYPH_RE.test(root.data)) swapText(root); return; }
  if (root.nodeType !== 1 || !GLYPH_RE.test(root.textContent || "")) return;
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const hits = [];
  for (let n = walk.nextNode(); n; n = walk.nextNode()) if (GLYPH_RE.test(n.data)) hits.push(n);
  hits.forEach(swapText);
}
export function upgradeGlyphs(root = document.body) {
  sweep(root);
  new MutationObserver((list) => {
    for (const m of list) {
      if (m.type === "characterData") sweep(m.target);
      else for (const n of m.addedNodes) sweep(n);
    }
  }).observe(root, { childList: true, subtree: true, characterData: true });
}
