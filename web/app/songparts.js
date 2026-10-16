// The parts of a song (intro, build, drop, breakdown, outro) from how loud
// it is, phrase by phrase - what a DJ hears and what the lights should
// follow.  Dance music moves in 8-bar phrases, so the song is cut there and
// each phrase is rated against the loudest one.  Pure: the timeline hands
// it the loudness envelope; the selftest runs it under Node.

const PART_ENERGY = { Intro: 0.25, Build: 0.65, Drop: 1.0, Breakdown: 0.35, Groove: 0.55, Outro: 0.2 };

/**
 * env: loudness per 1/rate s (RMS), bpm: the tempo, first: where beat 1 is (s).
 * -> [{t, name, energy}] in time order, the first one at the song's start.
 */
export function songParts(env, rate, bpm, first = 0) {
  if (!env || !env.length || !(bpm > 0)) return [];
  const duration = env.length / rate;
  const bar = 240 / bpm;
  let phrase = 8 * bar;
  if (duration / phrase < 6) phrase = 4 * bar;           // a short song: 4-bar phrases
  const starts = [];
  for (let t = Math.max(0, first % phrase); t < duration - phrase / 4; t += phrase) starts.push(t);
  if (!starts.length || starts[0] > 0.5) starts.unshift(0);
  const level = starts.map((t, i) => {
    const a = Math.floor(t * rate), b = Math.min(env.length, Math.floor((starts[i + 1] ?? duration) * rate));
    let s = 0;
    for (let k = a; k < b; k++) s += env[k] * env[k];
    return b > a ? Math.sqrt(s / (b - a)) : 0;
  });
  const top = Math.max(...level) || 1;
  const rel = level.map((v) => v / top);
  const loud = rel.map((v) => v >= 0.8);
  const firstLoud = loud.indexOf(true), lastLoud = loud.lastIndexOf(true);
  const kind = rel.map((v, i) => {
    if (loud[i]) return "Drop";
    if (firstLoud < 0) return "Groove";
    if (i < firstLoud) return loud[i + 1] && v >= 0.45 ? "Build" : "Intro";
    if (i > lastLoud) return "Outro";
    if (loud[i + 1] && v >= 0.45) return "Build";
    return v < 0.6 ? "Breakdown" : "Groove";
  });
  const parts = [];
  const seen = {};
  kind.forEach((k, i) => {
    if (parts.length && parts[parts.length - 1].kind === k) return;
    seen[k] = (seen[k] || 0) + 1;
    parts.push({ kind: k, t: Math.round(starts[i] * 100) / 100,
      name: seen[k] > 1 ? `${k} ${seen[k]}` : k, energy: PART_ENERGY[k] });
  });
  return parts.map(({ t, name, energy }) => ({ t, name, energy }));
}

/** The loudness envelope of decoded audio (an AudioBuffer's channel 0). */
export function loudness(samples, sampleRate, rate = 20) {
  const hop = Math.max(1, Math.floor(sampleRate / rate));
  const env = [];
  for (let i = 0; i + hop <= samples.length; i += hop) {
    let e = 0;
    for (let j = i; j < i + hop; j += 4) e += samples[j] * samples[j];
    env.push(Math.sqrt(e / (hop / 4)));
  }
  return env;
}

// ---------------------------------------------------------------------------
// What each part sounds like, in words for the show designer: how loud, the
// drums (a steady kick, half-time, none), the bass, a mid-range lead (a voice
// or a melody - the two can't be told apart from the sound alone), how bright,
// and whether it rises or falls away.  Three filtered bands of the song,
// 50 ms at a time, each part against the song's own average.

function biquad(kind, f, fs) {
  const w = 2 * Math.PI * f / fs, c = Math.cos(w), al = Math.sin(w) / (2 * Math.SQRT1_2);
  const a0 = 1 + al;
  const b = kind === "lp" ? [(1 - c) / 2, 1 - c, (1 - c) / 2] : [(1 + c) / 2, -(1 + c), (1 + c) / 2];
  return { b0: b[0] / a0, b1: b[1] / a0, b2: b[2] / a0, a1: -2 * c / a0, a2: (1 - al) / a0, x1: 0, x2: 0, y1: 0, y2: 0 };
}
function step(q, x) {
  const y = q.b0 * x + q.b1 * q.x1 + q.b2 * q.x2 - q.a1 * q.y1 - q.a2 * q.y2;
  q.x2 = q.x1; q.x1 = x; q.y2 = q.y1; q.y1 = y;
  return y;
}

/**
 * samples: the song (one channel), parts: [{t}] in the song's own seconds.
 * -> [{t, hear}] - one line per part, e.g. "loud; steady kick; heavy bass; bright".
 */
export function musicTraits(samples, sampleRate, parts, bpm = 120) {
  if (!samples || !samples.length || !parts || !parts.length) return [];
  const skip = sampleRate > 30000 ? 2 : 1;               // every other sample: highs to 11 kHz is plenty
  const fs = sampleRate / skip;
  const lo = biquad("lp", 150, fs), m1 = biquad("hp", 300, fs), m2 = biquad("lp", 3000, fs), hi = biquad("hp", 6000, fs);
  const hop = Math.max(1, Math.round(fs * 0.05));
  const frames = { all: [], bass: [], mid: [], high: [] };
  let n = 0, sa = 0, sb = 0, sm = 0, sh = 0;
  for (let i = 0; i < samples.length; i += skip) {
    const x = samples[i];
    const b = step(lo, x), m = step(m2, step(m1, x)), hh = step(hi, x);
    sa += x * x; sb += b * b; sm += m * m; sh += hh * hh;
    if (++n === hop) {
      frames.all.push(Math.sqrt(sa / n)); frames.bass.push(Math.sqrt(sb / n));
      frames.mid.push(Math.sqrt(sm / n)); frames.high.push(Math.sqrt(sh / n));
      n = sa = sb = sm = sh = 0;
    }
  }
  const F = frames.all.length;
  if (!F) return [];
  const fps = fs / hop;
  // kicks: the bass jumping well over its last second, at a local peak
  const bassTop = Math.max(...frames.bass) || 1e-9;
  const kick = new Uint8Array(F);
  for (let i = 1; i < F - 1; i++) {
    let avg = 0;
    const a = Math.max(0, i - 20);
    for (let k = a; k < i; k++) avg += frames.bass[k];
    avg /= Math.max(1, i - a);
    const v = frames.bass[i];
    if (v > 0.15 * bassTop && v > 1.35 * avg && v >= frames.bass[i - 1] && v > frames.bass[i + 1]) kick[i] = 1;
  }
  const mean = (arr, a, b) => { let s = 0; for (let k = a; k < b; k++) s += arr[k]; return b > a ? s / (b - a) : 0; };
  const spans = parts.map((p, i) => [Math.max(0, Math.floor(p.t * fps)), Math.min(F, Math.floor((parts[i + 1] ? parts[i + 1].t : F / fps) * fps))]);
  const st = spans.map(([a, b]) => {
    const all = mean(frames.all, a, b) || 1e-9;
    let kicks = 0;
    for (let k = a; k < b; k++) kicks += kick[k];
    const third = Math.max(1, Math.floor((b - a) / 3));
    return { all, bass: mean(frames.bass, a, b) / all, mid: mean(frames.mid, a, b) / all, high: mean(frames.high, a, b) / all,
      perBeat: b > a ? kicks / (((b - a) / fps) * (bpm / 60)) : 0,
      rise: mean(frames.all, b - third, b) / (mean(frames.all, a, a + third) || 1e-9), frames: b - a };
  });
  const top = Math.max(...st.map((s) => s.all)) || 1e-9;
  const avg = (key) => st.reduce((s, x) => s + x[key] * x.frames, 0) / (st.reduce((s, x) => s + x.frames, 0) || 1);
  const ab = avg("bass") || 1e-9, am = avg("mid") || 1e-9, ah = avg("high") || 1e-9;
  return parts.map((p, i) => {
    const s = st[i];
    if (!s.frames) return { t: p.t, hear: "" };
    const rel = s.all / top;
    const words = [rel < 0.35 ? "very quiet" : rel < 0.6 ? "quiet" : rel < 0.85 ? "medium loud" : "loud"];
    words.push(s.perBeat >= 0.6 && s.perBeat <= 1.4 ? "steady kick" : s.perBeat >= 0.3 ? "half-time drums"
      : s.perBeat >= 0.12 ? "sparse drums" : "no kick");
    if (s.bass / ab > 1.25) words.push("heavy bass");
    else if (s.bass / ab < 0.6) words.push("little bass");
    if (s.mid / am > 1.25) words.push("a strong mid-range lead (voice or melody)");
    if (s.high / ah > 1.3) words.push("bright (hats, cymbals, risers)");
    else if (s.high / ah < 0.6) words.push("dark, few highs");
    if (s.rise > 1.3) words.push("rising");
    else if (s.rise < 0.7) words.push("falling away");
    return { t: p.t, hear: words.join("; ").slice(0, 140) };
  });
}
