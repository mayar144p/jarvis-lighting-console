// The video render's choices (videorec.js has the screen): which file the
// browser can make, its name, how long it runs.  No screen here, so the
// selftest loads it in node.

// MP4 first: a client's phone and PowerPoint play it.  WebM where the
// browser can't make MP4 (older Chrome, Firefox).
export const FORMATS = [
  { mime: "video/mp4;codecs=avc1.640028", ext: "mp4" },
  { mime: "video/mp4", ext: "mp4" },
  { mime: "video/webm;codecs=vp9", ext: "webm" },
  { mime: "video/webm;codecs=vp8", ext: "webm" },
  { mime: "video/webm", ext: "webm" },
];

// with the timeline's music: the same, with a sound codec
export const FORMATS_AV = [
  { mime: "video/mp4;codecs=avc1.640028,mp4a.40.2", ext: "mp4", audio: true },
  { mime: "video/webm;codecs=vp9,opus", ext: "webm", audio: true },
  { mime: "video/webm;codecs=vp8,opus", ext: "webm", audio: true },
];

/** The first format this browser records, or null.  `audio`: one that
 *  carries sound too, when the browser has one (else picture only). */
export function pickFormat(isSupported, audio = false) {
  for (const f of audio ? [...FORMATS_AV, ...FORMATS] : FORMATS) {
    try { if (isSupported(f.mime)) return f; } catch (e) { /* a browser that throws: try the next */ }
  }
  return null;
}

export const SIZES = [
  { id: "hd", label: "Full HD (1920 wide)", long: 1920, bits: 12e6 },
  { id: "4k", label: "4K (3840 wide)", long: 3840, bits: 40e6 },
  { id: "small", label: "Small (1280 wide, for chat apps)", long: 1280, bits: 5e6 },
];

/** How long it records, in seconds: the timeline's length when it plays the
 *  timeline (+1 s so the last look lands), else what was asked (5 s .. 10 min). */
export function plannedSeconds(mode, asked, timelineLength) {
  if (mode === "timeline") return Math.max(1, Math.min(3600, Math.ceil(+timelineLength || 0) + 1));
  return Math.max(5, Math.min(600, Math.round(+asked || 30)));
}

/** "Club night-2026-10-08-21-30.mp4": the show's name, no characters a disk refuses. */
export function fileName(show, when, ext) {
  const base = String(show || "").replace(/\.json$/i, "").replace(/^.*[\\/]/, "").replace(/[^\w .-]+/g, "").trim().slice(0, 60) || "jarvis";
  const d = new Date(when);
  const p = (n) => String(n).padStart(2, "0");
  return `${base}-${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}-${p(d.getHours())}-${p(d.getMinutes())}.${ext}`;
}

export const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
