// LTC - SMPTE linear timecode, the audio kind (a "timecode track" from a
// playback computer, a video server or a multitrack): decoded from sound
// samples.  No screen and no audio here, so the selftest runs it in node
// with the encoder below.
//
// LTC is bi-phase mark: the level flips at every bit boundary, and a 1 has
// a second flip in the middle.  80 bits make a frame; the last 16 are the
// sync word 0011 1111 1111 1101, which says where a frame ends and which
// way the tape runs.  Time digits are BCD, least significant bit first.

const SYNC = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1];
const RATES = [24, 25, 29.97, 30];

const bcd = (bits, at, n) => { let v = 0; for (let i = 0; i < n; i++) v |= bits[at + i] << i; return v; };

/** Feed it samples; it calls onFrame({ seconds, text, fps, drop }) at the end of each frame. */
export class LtcDecoder {
  constructor(sampleRate, onFrame) {
    this.rate = sampleRate;
    this.onFrame = onFrame;
    this.level = 0;              // the signal's sign, with hysteresis
    this.since = 0;              // samples since the last flip
    this.period = sampleRate / (25 * 80);    // one bit, in samples (learned)
    this.half = false;           // a half bit seen (waiting for its pair)
    this.bits = [];
    this.lastAt = null;          // sample index of the last frame's end
    this.n = 0;                  // samples seen
    this.frameSamples = [];      // the last few frame lengths, for the rate
    this.peak = 0.05;
  }

  feed(samples) {
    for (let i = 0; i < samples.length; i++) {
      const x = samples[i];
      this.n++;
      this.since++;
      const a = Math.abs(x);
      this.peak = Math.max(this.peak * 0.9999, a);
      const th = this.peak * 0.25;         // hysteresis: noise around 0 isn't a flip
      const s = x > th ? 1 : x < -th ? -1 : 0;
      if (s && s !== this.level) {
        if (this.level) this._interval(this.since);
        this.level = s;
        this.since = 0;
      }
    }
  }

  _interval(len) {
    const p = this.period;
    if (len < p * 0.3 || len > p * 1.6) {          // noise, or a gap: start over
      this.half = false;
      if (len > p * 1.6 && len < p * 6) this.period = len * 0.8 + p * 0.2;   // a much slower or faster tape
      return;
    }
    if (len < p * 0.75) {                          // a half bit
      if (this.half) { this._bit(1); this.half = false; this.period = p * 0.9 + len * 2 * 0.1; }
      else this.half = true;
    } else {                                       // a whole bit
      if (this.half) { this.half = false; return; }   // lost step: resync on the next one
      this._bit(0);
      this.period = p * 0.9 + len * 0.1;
    }
  }

  _bit(b) {
    const bits = this.bits;
    bits.push(b);
    if (bits.length > 80) bits.shift();
    if (bits.length < 80) return;
    for (let i = 0; i < 16; i++) if (bits[64 + i] !== SYNC[i]) return;
    const fr = bcd(bits, 0, 4) + 10 * bcd(bits, 8, 2);
    const se = bcd(bits, 16, 4) + 10 * bcd(bits, 24, 3);
    const mi = bcd(bits, 32, 4) + 10 * bcd(bits, 40, 3);
    const ho = bcd(bits, 48, 4) + 10 * bcd(bits, 56, 2);
    const drop = !!bits[10];
    if (fr > 29 || se > 59 || mi > 59 || ho > 23) { this.bits = []; return; }
    // the frame rate from how long frames take (80 bits each)
    if (this.lastAt !== null) {
      this.frameSamples.push(this.n - this.lastAt);
      if (this.frameSamples.length > 8) this.frameSamples.shift();
    }
    this.lastAt = this.n;
    const avg = this.frameSamples.length ? this.frameSamples.reduce((a, b) => a + b, 0) / this.frameSamples.length : this.period * 80;
    const raw = this.rate / avg;
    let fps = RATES.reduce((best, r) => (Math.abs(r - raw) < Math.abs(best - raw) ? r : best), 25);
    if (drop) fps = 29.97;
    // the frame just ended: the time is the start of the next one
    const seconds = ho * 3600 + mi * 60 + se + (fr + 1) / Math.round(fps);
    const p2 = (v) => String(v).padStart(2, "0");
    this.bits = [];
    if (this.onFrame) this.onFrame({ seconds, fps, drop, text: `${p2(ho)}:${p2(mi)}:${p2(se)}${drop ? ";" : ":"}${p2(fr)}` });
  }
}

/** Samples of LTC for `frames` frames from `seconds` (tests, a test tone). */
export function encodeLtc(seconds, frames, fps = 25, sampleRate = 48000, amp = 0.5) {
  const out = [];
  const perBit = sampleRate / (fps * 80);
  let level = amp, carry = 0;
  const n = Math.round(fps);
  let total = Math.round(seconds * n);
  for (let f = 0; f < frames; f++, total++) {
    const fr = total % n, se = Math.floor(total / n) % 60, mi = Math.floor(total / n / 60) % 60, ho = Math.floor(total / n / 3600) % 24;
    const bits = new Array(80).fill(0);
    const put = (at, v, len) => { for (let i = 0; i < len; i++) bits[at + i] = (v >> i) & 1; };
    put(0, fr % 10, 4); put(8, Math.floor(fr / 10), 2);
    put(16, se % 10, 4); put(24, Math.floor(se / 10), 3);
    put(32, mi % 10, 4); put(40, Math.floor(mi / 10), 3);
    put(48, ho % 10, 4); put(56, Math.floor(ho / 10), 2);
    SYNC.forEach((b, i) => { bits[64 + i] = b; });
    for (const b of bits) {
      level = -level;                                  // a flip at every bit boundary
      const len = perBit + carry;
      const whole = Math.round(len);
      carry = len - whole;
      for (let i = 0; i < whole; i++) {
        if (b && i === Math.round(whole / 2)) level = -level;   // and mid-bit for a 1
        out.push(level);
      }
    }
  }
  return Float32Array.from(out);
}
