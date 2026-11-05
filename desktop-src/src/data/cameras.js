// Camera helpers shared by the wall and the big view.

/** Cameras ordered by distance from `origin`, the origin first, for stepping with the arrows. */
export function nearestCameras(items, origin, n = 40) {
  const cos = Math.cos((origin.lat * Math.PI) / 180)
  return items
    .filter(i => i.layer === 'cameras' && i.lat !== null && !i.cluster)
    .map(i => ({ item: i, d: i.id === origin.id ? -1 : (i.lat - origin.lat) ** 2 + (((((i.lon - origin.lon + 540) % 360) - 180) * cos) ** 2) }))
    .sort((a, b) => a.d - b.d)
    .slice(0, n)
    .map(x => x.item)
}
