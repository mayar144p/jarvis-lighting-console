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
