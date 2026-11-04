// A zone's outline as the 3D and the plot draw it (shared, no three.js).
/** What a zone shows on the floor: a wall's keep-out reaches far beyond the
 *  room (a beam aimed at the wall lands out there) - drawn as a band along
 *  that wall instead. */
export function zoneOutline(z, room) {
  if (z.kind !== "keepout" || !z.wall || !room || !room.width) return z.points;
  const x0 = (room.cx || 0) - room.width / 2, x1 = (room.cx || 0) + room.width / 2;
  const z0 = room.back || 0, z1 = z0 + (room.depth || 0), b = 0.35;
  return { left: [[x0, z0], [x0 + b, z0], [x0 + b, z1], [x0, z1]], right: [[x1 - b, z0], [x1, z0], [x1, z1], [x1 - b, z1]],
    back: [[x0, z0], [x1, z0], [x1, z0 + b], [x0, z0 + b]], front: [[x0, z1 - b], [x1, z1 - b], [x1, z1], [x0, z1]] }[z.wall] || z.points;
}
