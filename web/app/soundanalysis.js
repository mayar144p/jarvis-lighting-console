// What the room sounds like, from one analyser frame at a time: loudness
// and three bands (each with its own automatic gain, so a quiet laptop mic
// and a hot line-in both read 0..1), beats (the bass jumping well above
// its recent average), the tempo (the commonest gap between beats, folded
// into 80..160 BPM) and drops (after a breakdown - the bass well under its
// long average for 3 s - the bass coming back hard).
//
// Pure: frames and times go in, a reading comes out; the browser plumbing
// is in soundin.js.  So it runs under node for the tests.

export const BANDS = { bass: [30, 150], mid: [150, 2000], high: [2000, 12000] };

export function createAnalysis(sampleRate, binCount) {
  const peak = { level: 0.02, bass: 1e-9, mid: 1e-9, high: 1e-9 };
  const hist = [];
  let lastBeat = -1e9, beats = [];
  let longBass = 0, shortBass = 0, quietSince = 0, lastDrop = -1e9, breakdown = false, frames = 0;
  const reading = { level: 0, bass: 0, mid: 0, high: 0, bpm: null, confidence: 0, beatAt: -1e9, dropAt: -1e9 };
  const nyq = sampleRate / 2;
  const range = (lo, hi) => [Math.max(1, Math.floor(lo / nyq * binCount)), Math.min(binCount - 1, Math.ceil(hi / nyq * binCount))];
  const bins = { bass: range(...BANDS.bass), mid: range(...BANDS.mid), high: range(...BANDS.high) };

  const power = (freqDb, [a, b]) => {
    let sum = 0;
    for (let i = a; i <= b; i++) sum += Math.pow(10, freqDb[i] / 10);
    return sum / Math.max(1, b - a + 1);
  };
  // the peak falls slowly (automatic gain); it starts from the first
  // second's loudest, so the opening frames don't all read "full"
  const norm = (key, v) => {
    peak[key] = Math.max(v, peak[key] * 0.997);
    return Math.max(0, Math.min(1, v / (peak[key] || 1e-12)));
  };

  function estimateBpm() {
    if (beats.length < 6) return;
    const fold = (g) => { let b = 60000 / g; while (b < 80) b *= 2; while (b > 160) b /= 2; return b; };
    const gaps = [];
    for (let i = 1; i < beats.length; i++) gaps.push(beats[i] - beats[i - 1]);
    const votes = new Map();
    for (const g of gaps) {
      const k = Math.round(fold(g));
      for (const kk of [k - 1, k, k + 1]) votes.set(kk, (votes.get(kk) || 0) + (kk === k ? 2 : 1));
    }
    let best = 0, score = 0;
    for (const [k, v] of votes) if (v > score) { best = k; score = v; }
    // then the tempo from every gap that is a whole number of beats at
    // about that tempo: total time over total beats, so a beat heard a
    // frame early and the next a frame late cancel out
    const period = 60000 / best;
    let time = 0, count = 0, agree = 0;
    for (const g of gaps) {
      // a gap of half a (folded) beat counts half: double-time music
      let gf = g, share = 1;
      while (gf < period * 0.7) { gf *= 2; share /= 2; }
      const m = Math.max(1, Math.round(gf / period));
      if (Math.abs(gf / m - period) < period * 0.12) { time += g; count += m * share; agree++; }
    }
    if (!count) return;
    let bpm = 60000 * count / time;
    while (bpm < 80) bpm *= 2;
    while (bpm > 160) bpm /= 2;
    reading.bpm = Math.round(bpm * 10) / 10;
    reading.confidence = Math.round(agree / gaps.length * 100) / 100;
  }

  /** One frame: freqDb (the analyser's dB spectrum), wave (time domain), now (ms). */
  function step(freqDb, wave, now) {
    frames++;
    let rms = 0;
    for (let i = 0; i < wave.length; i++) rms += wave[i] * wave[i];
    rms = Math.sqrt(rms / Math.max(1, wave.length));
    const bp = power(freqDb, bins.bass);
    reading.level = norm("level", rms);
    reading.bass = norm("bass", bp);
    reading.mid = norm("mid", power(freqDb, bins.mid));
    reading.high = norm("high", power(freqDb, bins.high));
    const out = { beat: false, drop: false };
    hist.push(bp);
    if (hist.length > 38) hist.shift();
    const mean = hist.reduce((a, b) => a + b, 0) / hist.length;
    if (hist.length > 12 && bp > mean * 1.45 && reading.bass > 0.3 && now - lastBeat > 280) {
      if (beats.length && now - beats[beats.length - 1] > 2500) beats = [];      // the music stopped
      lastBeat = now;
      reading.beatAt = now;
      out.beat = true;
      beats.push(now);
      if (beats.length > 24) beats.shift();
      estimateBpm();
    }
    longBass = frames === 1 ? reading.bass : longBass * 0.995 + reading.bass * 0.005;
    shortBass = shortBass * 0.8 + reading.bass * 0.2;
    if (shortBass < longBass * 0.35) {
      if (!quietSince) quietSince = now;
      if (now - quietSince > 3000) breakdown = true;
    } else {
      quietSince = 0;
    }
    if (breakdown && shortBass > longBass * 1.15 && reading.bass > 0.5 && now - lastDrop > 10000) {
      breakdown = false;
      lastDrop = now;
      reading.dropAt = now;
      out.drop = true;
    }
    return out;
  }

  return { step, reading };
}
