// Colour picker: a hue ring around a saturation/brightness square.
// Drag either; arrow keys nudge (Shift = bigger steps).

export function hsvToRgb(h, s, v) {
  const f = (n) => {
    const k = (n + h / 60) % 6;
    return v - v * s * Math.max(0, Math.min(k, 4 - k, 1));
  };
  return [f(5), f(3), f(1)].map((x) => Math.round(x * 255));
}

export function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let hh = 0;
  if (d) {
    if (max === r) hh = ((g - b) / d) % 6;
    else if (max === g) hh = (b - r) / d + 2;
    else hh = (r - g) / d + 4;
    hh *= 60;
    if (hh < 0) hh += 360;
  }
  return [hh, max ? d / max : 0, max];
}

export const hexToRgb = (hex) => {
  const s = String(hex || "").replace("#", "");
  const full = s.length === 3 ? s.split("").map((c) => c + c).join("") : s;
  if (!/^[0-9a-f]{6}$/i.test(full)) return null;
  const n = parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};

export const rgbToHex = (r, g, b) => "#" + [r, g, b].map((x) => x.toString(16).padStart(2, "0")).join("");

export function createPicker(canvas, onPick) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width, C = W / 2;
  const R1 = W * 0.48, R0 = W * 0.38;              // hue ring radii
  const SQ = R0 * Math.SQRT2 * 0.92;                 // SV square side
  const sq0 = C - SQ / 2;
  let hsv = [0, 0, 1];
  let drag = null;

  const ring = document.createElement("canvas");
  ring.width = ring.height = W;
  {
    const g = ring.getContext("2d");
    for (let a = 0; a < 360; a += 0.5) {
      const r0 = (a - 0.6) * Math.PI / 180, r1 = (a + 0.6) * Math.PI / 180;
      g.beginPath();
      g.arc(C, C, R1, r0, r1);
      g.arc(C, C, R0, r1, r0, true);
      g.closePath();
      g.fillStyle = `hsl(${a},100%,50%)`;
      g.fill();
    }
  }

  function draw() {
    ctx.clearRect(0, 0, W, W);
    ctx.drawImage(ring, 0, 0);
    const [r, gg, b] = hsvToRgb(hsv[0], 1, 1);
    const gx = ctx.createLinearGradient(sq0, 0, sq0 + SQ, 0);
    gx.addColorStop(0, "#fff");
    gx.addColorStop(1, `rgb(${r},${gg},${b})`);
    ctx.fillStyle = gx;
    ctx.fillRect(sq0, sq0, SQ, SQ);
    const gy = ctx.createLinearGradient(0, sq0, 0, sq0 + SQ);
    gy.addColorStop(0, "rgba(0,0,0,0)");
    gy.addColorStop(1, "#000");
    ctx.fillStyle = gy;
    ctx.fillRect(sq0, sq0, SQ, SQ);
    const a = hsv[0] * Math.PI / 180;
    const rm = (R0 + R1) / 2;
    ring_marker(C + Math.cos(a) * rm, C + Math.sin(a) * rm);
    ring_marker(sq0 + hsv[1] * SQ, sq0 + (1 - hsv[2]) * SQ);
  }
  function ring_marker(x, y) {
    ctx.lineWidth = 3;
    ctx.strokeStyle = "#fff";
    ctx.beginPath();
    ctx.arc(x, y, W * 0.022, 0, Math.PI * 2);
    ctx.stroke();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "#000";
    ctx.beginPath();
    ctx.arc(x, y, W * 0.022 + 2, 0, Math.PI * 2);
    ctx.stroke();
  }
  const emit = (final) => {
    draw();
    onPick(rgbToHex(...hsvToRgb(...hsv)), final);
  };
  function at(e) {
    const r = canvas.getBoundingClientRect();
    return [(e.clientX - r.left) * W / r.width, (e.clientY - r.top) * W / r.height];
  }
  function apply(x, y) {
    if (drag === "ring") {
      let a = Math.atan2(y - C, x - C) * 180 / Math.PI;
      if (a < 0) a += 360;
      hsv[0] = a;
    } else {
      hsv[1] = Math.max(0, Math.min(1, (x - sq0) / SQ));
      hsv[2] = Math.max(0, Math.min(1, 1 - (y - sq0) / SQ));
    }
  }
  canvas.addEventListener("pointerdown", (e) => {
    const [x, y] = at(e);
    const d = Math.hypot(x - C, y - C);
    if (d >= R0 - 4 && d <= R1 + 6) drag = "ring";
    else if (x >= sq0 - 6 && x <= sq0 + SQ + 6 && y >= sq0 - 6 && y <= sq0 + SQ + 6) drag = "sq";
    else return;
    canvas.setPointerCapture(e.pointerId);
    apply(x, y);
    emit(false);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const [x, y] = at(e);
    apply(x, y);
    emit(false);
  });
  const end = () => { if (drag) { drag = null; emit(true); } };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.tabIndex = 0;
  canvas.addEventListener("keydown", (e) => {
    const k = e.shiftKey ? 10 : 2;
    if (e.key === "ArrowLeft") hsv[0] = (hsv[0] - k + 360) % 360;
    else if (e.key === "ArrowRight") hsv[0] = (hsv[0] + k) % 360;
    else if (e.key === "ArrowUp") hsv[1] = Math.min(1, hsv[1] + k / 100);
    else if (e.key === "ArrowDown") hsv[1] = Math.max(0, hsv[1] - k / 100);
    else return;
    e.preventDefault();
    e.stopPropagation();
    emit(true);
  });
  draw();
  return {
    set(hex) {
      const rgb = hexToRgb(hex);
      if (!rgb || drag) return;
      const next = rgbToHsv(...rgb);
      if (next[1] < 0.01) next[0] = hsv[0];         // keep the hue for greys
      hsv = next;
      draw();
    },
  };
}
