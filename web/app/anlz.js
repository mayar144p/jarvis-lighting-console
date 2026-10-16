// rekordbox's analysis files (ANLZ0000.DAT / .EXT), the little the desk
// needs: the tagged sections, and the track's path.  Pure, so the selftest
// runs it under Node; rekordbox.js has the screen, app/rekordbox.py reads
// the sections.

/** The tagged sections of an analysis file (.DAT / .EXT) we want: {fourcc: Uint8Array}. Pure. */
export function pickTags(buf, want = ["PPTH", "PQTZ", "PSSI"]) {
  const v = new DataView(buf);
  const out = {};
  if (buf.byteLength < 12 || String.fromCharCode(v.getUint8(0), v.getUint8(1), v.getUint8(2), v.getUint8(3)) !== "PMAI") return out;
  let pos = v.getUint32(4);
  while (pos + 12 <= buf.byteLength) {
    const kind = String.fromCharCode(v.getUint8(pos), v.getUint8(pos + 1), v.getUint8(pos + 2), v.getUint8(pos + 3));
    const size = v.getUint32(pos + 8);
    if (size < 12 || pos + size > buf.byteLength) break;
    if (want.includes(kind) && !out[kind]) out[kind] = new Uint8Array(buf, pos, size);
    pos += size;
  }
  return out;
}

/** The track's file name from its PPTH tag (UTF-16, big-endian). Pure. */
export function pathOf(tag) {
  if (!tag) return "";
  const v = new DataView(tag.buffer, tag.byteOffset, tag.byteLength);
  const head = v.getUint32(4), n = v.getUint32(12);
  let s = "";
  for (let i = head; i + 1 < Math.min(tag.byteLength, head + n); i += 2) {
    const c = v.getUint16(i);
    if (!c) break;
    s += String.fromCharCode(c);
  }
  return s;
}
