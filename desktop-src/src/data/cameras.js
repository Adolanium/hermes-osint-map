// Camera helpers shared by the wall and the big view: which cameras belong together, and in what
// order. Both are scoped to the operator that published the camera. A city whose feed is short, or
// whose stills are failing, must never borrow another operator's cameras: Singapore's LTA feed
// publishes eight cameras, and when its image host refused every still the wall filled its ninth
// tile — and then the rest — with Hong Kong cameras 2,500 km away, under a Singapore label.

/** How many candidates the wall looks at, so a camera that is down can be swapped for the next
 *  nearest of the same operator. */
export const POOL_SIZE = 30

/** The open-data source a camera item came from ("tfl", "sg", "ca", "hk"), read from its id. */
export function cameraSource(item) {
  const [layer, source] = String(item?.id ?? '').split(':')
  return layer === 'cameras' && source && source !== 'cluster' ? source : null
}

/** True when both cameras came from the same operator's feed. Items carry `country`, which
 *  survives clustering; rows built without one are compared by the source segment of their id. */
export function sameOperator(a, b) {
  if (a?.country && b?.country) return a.country === b.country
  const source = cameraSource(a)
  return source !== null && source === cameraSource(b)
}

/** Cameras ordered by distance from `origin`, the origin first, for stepping with the arrows.
 *  Only the origin's own operator, so the arrows never step into another country's feed. */
export function nearestCameras(items, origin, n = 40) {
  const cos = Math.cos((origin.lat * Math.PI) / 180)
  return items
    .filter(i => i.layer === 'cameras' && i.lat !== null && !i.cluster && sameOperator(i, origin))
    .map(i => ({ item: i, d: i.id === origin.id ? -1 : (i.lat - origin.lat) ** 2 + (((((i.lon - origin.lon + 540) % 360) - 180) * cos) ** 2) }))
    .sort((a, b) => a.d - b.d)
    .slice(0, n)
    .map(x => x.item)
}

/** The cluster's cameras nearest its centre, so the wall shows one neighbourhood — and only that
 *  cluster's operator, so a short or failing feed cannot be padded with another country's stills. */
export function wallCameras(items, center, n = POOL_SIZE) {
  const cos = Math.cos((center.lat * Math.PI) / 180)
  return items
    .filter(i => i.layer === 'cameras' && i.lat !== null && !i.cluster && sameOperator(i, center))
    .map(i => ({ item: i, d: (i.lat - center.lat) ** 2 + (((((i.lon - center.lon + 540) % 360) - 180) * cos) ** 2) }))
    .sort((a, b) => a.d - b.d)
    .slice(0, n)
    .map(x => x.item)
}
