// A 3DS reader that cannot hang.
//
// three.js's TDSLoader walks the chunk tree and trusts every length; the
// 3DS files inside real GDTF archives (and our own test files) send it into
// an endless loop.  This scanner reads by SELF-VALIDATION instead, the way
// the previous Jarvis twin did, and was measured against real files:
//
//   * a VERTICES chunk (0x4110) is accepted only when its length is exactly
//     8 + count * 12;
//   * a FACES chunk (0x4120) records are four uint16s - i, j, k, flags LAST
//     - and the chunk may be longer than its faces, so its length is a
//     ceiling, not an equality;
//   * a faces chunk belongs to the vertices before it when every index
//     names a real vertex.  That range check is what tells a faces chunk
//     from the other chunks in the file that happen to have a fitting size.
//
// Every count is bounded by the file's length before anything is allocated,
// and the scan only ever moves forward, so a malformed file ends the loop.
import * as THREE from "three";

export function parse3DS(buffer) {
  const buf = buffer instanceof ArrayBuffer ? buffer
    : buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
  const dv = new DataView(buf);
  const group = new THREE.Group();
  if (buf.byteLength < 8) return group;
  const head = dv.getUint16(0, true);
  if (head !== 0x4D4D && head !== 0x4D3D) return group;
  const u16 = (o) => dv.getUint16(o, true);
  const u32 = (o) => dv.getUint32(o, true);
  let verts = null;
  let nv = 0;
  for (let o = 0; o + 8 <= buf.byteLength; o++) {
    const len = u32(o + 2);
    if (len < 8 || o + len > buf.byteLength) continue;
    const id = u16(o);
    const n = u16(o + 6);
    if (id === 0x4110 && n > 0 && 8 + n * 12 === len) {
      verts = new Float32Array(n * 3);
      for (let i = 0; i < n * 3; i++) verts[i] = dv.getFloat32(o + 8 + i * 4, true);
      nv = n;
      o += len - 1;
    } else if (id === 0x4120 && verts && n > 0 && o + 8 + n * 8 <= buf.byteLength) {
      const idx = new Uint32Array(n * 3);
      let ok = true;
      for (let f = 0; f < n && ok; f++) {
        const base = o + 8 + f * 8;
        for (let k = 0; k < 3; k++) {
          const v = u16(base + k * 2);
          if (v >= nv) { ok = false; break; }
          idx[f * 3 + k] = v;
        }
      }
      if (!ok) continue;
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(verts, 3));
      g.setIndex(new THREE.BufferAttribute(idx, 1));
      g.computeVertexNormals();
      group.add(new THREE.Mesh(g));
      verts = null;
      o += 8 + n * 8 - 1;
    }
  }
  return group;
}
