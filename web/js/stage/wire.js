// The flat views (Top / Front / Side) are line drawings, white on black, as
// on a pro visualiser: every solid swaps its surface for one that only
// hides what is behind it, and gets its edges drawn as lines.  The lines
// are children of the mesh, so a moving head's lines move with it.
import * as THREE from "three";

export const WIRE = new THREE.LineBasicMaterial({ color: 0xf2f2f5, toneMapped: false });
export const WIRE_ROOM = new THREE.LineBasicMaterial({ color: 0x9a9aa4, toneMapped: false });
export const WIRE_SEL = new THREE.LineBasicMaterial({ color: 0x2fd6f5, toneMapped: false });
// fills nothing, but still hides the lines behind it (pushed back a hair so
// its own edges are never hidden by it)
const HIDE = new THREE.MeshBasicMaterial({ colorWrite: false, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });

function invisible(m) {
  if (Array.isArray(m)) return m.every(invisible);
  return !m || m.visible === false;
}

/** Turn a part of the scene into a line drawing (on) or back (off).
 *  `mat` is the line colour for this part. */
export function wireUp(root, on, mat = WIRE) {
  const meshes = [];
  root.traverse((o) => { if (o.isMesh && !o.isSprite) meshes.push(o); });
  for (const o of meshes) {
    const ud = o.userData;
    if (on) {
      if (ud.wireOrig !== undefined) continue;                 // already drawn as lines
      if (invisible(o.material) || ud.wireSkip) continue;      // click volumes, rings
      ud.wireOrig = o.material;
      o.material = HIDE;
      // a crowd of thousands, a floor plan, the ground round the room:
      // hidden, no lines
      if (o.isInstancedMesh || ud.wireBare || ud.crowd || ud.performer || ud.venueKind === "underlay") continue;
      if (!ud.wireLines) {
        const lines = new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry, 28), mat);
        lines.userData.wireOf = true;
        lines.raycast = () => {};                               // never picked
        ud.wireLines = lines;
        o.add(lines);
      }
      ud.wireLines.material = mat;
      ud.wireLines.visible = true;
    } else {
      if (ud.wireOrig === undefined) continue;
      o.material = ud.wireOrig;
      delete ud.wireOrig;
      if (ud.wireLines) ud.wireLines.visible = false;
    }
  }
}

/** The colour of one light's lines (selected lights in the accent). */
export function wireTint(root, mat) {
  root.traverse((o) => { if (o.userData && o.userData.wireLines) o.userData.wireLines.material = mat; });
}
