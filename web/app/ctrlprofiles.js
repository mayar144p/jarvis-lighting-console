// Ready layouts for hardware controllers (backlog A10 item 1): which pad,
// fader and button is which, and the bytes that light a pad, blink a
// button or move a motor fader.  Pure data and functions - no screen, no
// MIDI port - so the selftest checks every byte (tools/selftests).
//
// Every layout speaks the same small language to the desk:
//   decode(bytes) -> { pad: i, down } | { fader: i, value 0..1 } | { master: value }
//                    | { go: i, down } | { release: i, down } | { page: i, down } | { touch: i, down } | null
//   padLed(i, rgb [0..255 x3] | null, on) / buttonLed(kind, i, on) / faderOut(i, value) -> [bytes...]
// Pads are counted from the top-left, row by row (as the buttons page is).

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// the nearest of a few fixed LED colours (old one-colour-family pads)
function nearest(rgb, table) {
  let best = table[0], dist = Infinity;
  for (const t of table) {
    const d = (t.rgb[0] - rgb[0]) ** 2 + (t.rgb[1] - rgb[1]) ** 2 + (t.rgb[2] - rgb[2]) ** 2;
    if (d < dist) { dist = d; best = t; }
  }
  return best;
}

// Akai / Novation 128-colour palette, the handful we need (index -> rgb)
const PALETTE = [
  { v: 3, rgb: [255, 255, 255] }, { v: 5, rgb: [255, 0, 0] }, { v: 9, rgb: [255, 120, 0] }, { v: 13, rgb: [255, 255, 0] },
  { v: 21, rgb: [0, 255, 0] }, { v: 33, rgb: [0, 255, 255] }, { v: 37, rgb: [0, 160, 255] }, { v: 45, rgb: [0, 0, 255] },
  { v: 49, rgb: [130, 0, 255] }, { v: 53, rgb: [255, 0, 255] }, { v: 57, rgb: [255, 0, 120] }, { v: 9, rgb: [255, 170, 60] },
];

// --------------------------------------------------------------- Akai APC mini
// 8x8 pads, notes 0-63 from the BOTTOM-left; 9 faders CC 48-56 (56 = master);
// 8 round buttons under the pads (notes 64-71) and 8 down the side (82-89).
// Pad LEDs: velocity 1 green, 3 red, 5 yellow (+1 = blinking), 0 off.
const APC1 = [{ v: 1, rgb: [0, 255, 0] }, { v: 3, rgb: [255, 0, 0] }, { v: 5, rgb: [255, 200, 0] }];
const apcNote = (i) => (7 - Math.floor(i / 8)) * 8 + (i % 8);
const apcPad = (note) => (7 - Math.floor(note / 8)) * 8 + (note % 8);

export const PROFILES = {
  apcmini: {
    name: "Akai APC mini", pads: 64, cols: 8, faders: 8, hasMaster: true, motor: false,
    match: (n) => /apc\s*mini/i.test(n) && !/mk\s*2/i.test(n),
    hello: () => [],
    decode([st, d1, d2]) {
      const kind = st & 0xf0, down = kind === 0x90 && d2 > 0;
      if (kind === 0xb0 && d1 >= 48 && d1 <= 56) return d1 === 56 ? { master: d2 / 127 } : { fader: d1 - 48, value: d2 / 127 };
      if (kind !== 0x90 && kind !== 0x80) return null;
      if (d1 <= 63) return { pad: apcPad(d1), down };
      if (d1 >= 64 && d1 <= 71) return { go: d1 - 64, down };
      if (d1 >= 82 && d1 <= 89) return { page: d1 - 82, down };
      return null;
    },
    padLed(i, rgb, on) {
      if (!rgb) return [[0x90, apcNote(i), 0]];
      return [[0x90, apcNote(i), nearest(rgb, APC1).v + (on ? 1 : 0)]];     // on = blinking in its colour
    },
    buttonLed(kind, i, on) {
      const base = kind === "go" ? 64 : kind === "page" ? 82 : null;
      return base === null || i > 7 ? [] : [[0x90, base + i, on ? 1 : 0]];
    },
    faderOut: () => [],
  },

  // ------------------------------------------------------- Akai APC mini mk2
  // The same grid, RGB pads: the MIDI channel is the brightness (0x90 = 10 %
  // ... 0x96 = 100 %), the velocity the palette colour.  Buttons under the
  // pads 100-107, down the side 112-119.
  apcmini2: {
    name: "Akai APC mini mk2", pads: 64, cols: 8, faders: 8, hasMaster: true, motor: false,
    match: (n) => /apc\s*mini/i.test(n) && /mk\s*2/i.test(n),
    hello: () => [],
    decode([st, d1, d2]) {
      const kind = st & 0xf0, down = kind === 0x90 && d2 > 0;
      if (kind === 0xb0 && d1 >= 48 && d1 <= 56) return d1 === 56 ? { master: d2 / 127 } : { fader: d1 - 48, value: d2 / 127 };
      if (kind !== 0x90 && kind !== 0x80) return null;
      if (d1 <= 63) return { pad: apcPad(d1), down };
      if (d1 >= 100 && d1 <= 107) return { go: d1 - 100, down };
      if (d1 >= 112 && d1 <= 119) return { page: d1 - 112, down };
      return null;
    },
    padLed(i, rgb, on) {
      if (!rgb) return [[0x90, apcNote(i), 0]];
      return [[on ? 0x96 : 0x91, apcNote(i), nearest(rgb, PALETTE).v]];   // 100 % on, 25 % off
    },
    buttonLed(kind, i, on) {
      const base = kind === "go" ? 100 : kind === "page" ? 112 : null;
      return base === null || i > 7 ? [] : [[0x90, base + i, on ? 1 : 0]];
    },
    faderOut: () => [],
  },
};

// ------------------------------------------------- Novation Launchpad (MK3 / X)
// Programmer mode (a SysEx switches it on, another back off): pads are
// notes 11-88 (row * 10 + column, row 1 at the BOTTOM); the round buttons
// along the top are CC 91-98, down the right side CC 89, 79 ... 19.  LEDs
// in any colour by SysEx (RGB 0-127), so a pad shows its button's tile
// colour - dim when off, full when on.
function launchpad(name, device, re) {
  const sx = (...b) => [0xf0, 0x00, 0x20, 0x29, 0x02, device, ...b, 0xf7];
  const padNote = (i) => (8 - Math.floor(i / 8)) * 10 + (i % 8) + 1;
  return {
    name, pads: 64, cols: 8, faders: 0, hasMaster: false, motor: false,
    match: (n) => re.test(n),
    hello: () => [sx(0x0e, 0x01)],               // programmer mode
    bye: () => [sx(0x0e, 0x00)],                 // back to the Launchpad's own mode
    decode([st, d1, d2]) {
      const kind = st & 0xf0;
      if (kind === 0xb0) {
        if (d1 >= 91 && d1 <= 98) return { go: d1 - 91, down: d2 > 0 };
        if (d1 % 10 === 9 && d1 >= 19 && d1 <= 89) return { page: (89 - d1) / 10, down: d2 > 0 };
        return null;
      }
      if (kind !== 0x90 && kind !== 0x80) return null;
      const row = Math.floor(d1 / 10), col = d1 % 10;
      if (row < 1 || row > 8 || col < 1 || col > 8) return null;
      return { pad: (8 - row) * 8 + col - 1, down: kind === 0x90 && d2 > 0 };
    },
    padLed(i, rgb, on) {
      const c = rgb ? rgb.map((v) => clamp(Math.round((v / 255) * 127 * (on ? 1 : 0.14)), 0, 127)) : [0, 0, 0];
      return [sx(0x03, 0x03, padNote(i), ...c)];
    },
    buttonLed(kind, i, on) {
      if (i > 7) return [];
      const led = kind === "go" ? 91 + i : kind === "page" ? 89 - i * 10 : null;
      if (led === null) return [];
      return [sx(0x03, 0x03, led, ...(on ? [127, 127, 127] : [10, 10, 10]))];
    },
    faderOut: () => [],
  };
}
PROFILES.launchpadmini3 = launchpad("Novation Launchpad Mini MK3", 0x0d, /launchpad\s*mini\s*mk\s*3|lpminimk3/i);
PROFILES.launchpadx = launchpad("Novation Launchpad X", 0x0c, /launchpad\s*x\b|lpx\b/i);

// ---------------------------------------- Mackie Control (Behringer X-Touch...)
// 8 motor faders (pitch bend on channels 1-8) and the master (channel 9),
// 14-bit; buttons by note: REC 0-7, SOLO 8-15, MUTE 16-23, SELECT 24-31,
// fader touch 104-112.  LEDs: velocity 127 on, 0 off.  The faders are the
// playbacks and the grand master, and they MOVE when the desk changes them.
// SELECT = GO, MUTE = release, REC = the buttons page's first 8 buttons,
// SOLO = pick the buttons page.
PROFILES.mackie = {
  name: "Mackie Control (Behringer X-Touch / X-Touch Compact)", pads: 8, cols: 8, faders: 8, hasMaster: true, motor: true,
  match: (n) => /x-?touch|mackie|mcu/i.test(n) && !/mini/i.test(n),
  hello: () => [],
  decode([st, d1, d2]) {
    const kind = st & 0xf0, ch = st & 0x0f;
    if (kind === 0xe0) {
      const v = ((d2 << 7) | d1) / 16383;
      return ch === 8 ? { master: v } : ch < 8 ? { fader: ch, value: v } : null;
    }
    if (kind !== 0x90 && kind !== 0x80) return null;
    const down = kind === 0x90 && d2 > 0;
    if (d1 <= 7) return { pad: d1, down };
    if (d1 <= 15) return { page: d1 - 8, down };
    if (d1 <= 23) return { release: d1 - 16, down };
    if (d1 <= 31) return { go: d1 - 24, down };
    if (d1 >= 104 && d1 <= 112) return { touch: d1 - 104, down };
    return null;
  },
  padLed: (i, rgb, on) => (i > 7 ? [] : [[0x90, i, rgb && on ? 127 : 0]]),
  buttonLed(kind, i, on) {
    const base = { go: 24, release: 16, page: 8 }[kind];
    return base === undefined || i > 7 ? [] : [[0x90, base + i, on ? 127 : 0]];
  },
  faderOut(i, value) {                              // i = 8: the master
    const v = clamp(Math.round(value * 16383), 0, 16383);
    return [[0xe0 + clamp(i, 0, 8), v & 0x7f, v >> 7]];
  },
};

// --------------------------------------------------- Akai APC40 (mk1 and mkII)
// From Akai's published protocol (not yet tried on a real unit: Settings ->
// Controllers -> MIDI monitor shows what it sends).  A SysEx puts it in
// "Ableton Live" mode, so the desk lights every LED.  A 5 x 8 clip grid,
// 8 track faders (CC 7 on channels 1-8) and the master (CC 14); under each
// column TRACK SELECT (note 51 on that column's channel) = GO and CLIP STOP
// (note 52) = release; the 5 SCENE LAUNCH buttons (notes 82-86) = pages.
function apc40(name, product, re, grid) {
  const sx = (mode) => [0xf0, 0x47, 0x7f, product, 0x60, 0x00, 0x04, mode,
    ...(product === 0x29 ? [0x09, 0x07, 0x01] : [0x08, 0x02, 0x01]), 0xf7];
  return {
    name, pads: 40, cols: 8, faders: 8, hasMaster: true, motor: false,
    match: (n) => re.test(n),
    hello: () => [sx(0x41)],                     // Ableton Live mode: the host lights the LEDs
    bye: () => [sx(0x40)],                       // back to its own (generic) mode
    decode([st, d1, d2]) {
      const kind = st & 0xf0, ch = st & 0x0f;
      if (kind === 0xb0) {
        if (d1 === 7 && ch < 8) return { fader: ch, value: d2 / 127 };
        if (d1 === 14 && ch === 0) return { master: d2 / 127 };
        return null;
      }
      if (kind !== 0x90 && kind !== 0x80) return null;
      const down = kind === 0x90 && d2 > 0;
      const pad = grid.pad(ch, d1);
      if (pad !== null) return { pad, down };
      if (d1 === 51 && ch < 8) return { go: ch, down };
      if (d1 === 52 && ch < 8) return { release: ch, down };
      if (d1 >= 82 && d1 <= 86 && ch === 0) return { page: d1 - 82, down };
      return null;
    },
    padLed: (i, rgb, on) => [grid.led(i, rgb, on)],
    buttonLed(kind, i, on) {
      if (kind === "page") return i > 4 ? [] : [[0x90, 82 + i, on ? 1 : 0]];
      const note = { go: 51, release: 52 }[kind];
      return note === undefined || i > 7 ? [] : [[0x90 + i, note, on ? 1 : 0]];
    },
    faderOut: () => [],
  };
}
// mk1: the grid is notes 53-57 (top row first) on the column's own channel;
// LEDs green / red / yellow, +1 blinking (on) - like the first APC mini
PROFILES.apc40 = apc40("Akai APC40", 0x73, /apc\s*40(?!.*mk\s*(2|ii))/i, {
  pad: (ch, d1) => (ch < 8 && d1 >= 53 && d1 <= 57 ? (d1 - 53) * 8 + ch : null),
  led: (i, rgb, on) => [0x90 + (i % 8), 53 + Math.floor(i / 8), rgb ? nearest(rgb, APC1).v + (on ? 1 : 0) : 0],
});
// mkII: RGB pads, notes 0-39 from the BOTTOM-left on channel 1, the colour
// from the 128-colour palette; on, it pulses (channel 9)
PROFILES.apc40mk2 = apc40("Akai APC40 mkII", 0x29, /apc\s*40.*mk\s*(2|ii)/i, {
  pad: (ch, d1) => (ch === 0 && d1 <= 39 ? (4 - Math.floor(d1 / 8)) * 8 + (d1 % 8) : null),
  led: (i, rgb, on) => [rgb && on ? 0x98 : 0x90, (4 - Math.floor(i / 8)) * 8 + (i % 8), rgb ? nearest(rgb, PALETTE).v : 0],
});

/** The layout for a MIDI port's name, or null. */
export function profileFor(name) {
  for (const [id, p] of Object.entries(PROFILES)) if (p.match(String(name || ""))) return id;
  return null;
}

/** "#ff8800" -> [255, 136, 0] */
export function hexRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
