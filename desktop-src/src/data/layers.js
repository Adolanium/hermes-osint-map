// The map's layers as the page sees them: how each looks (shape + theme colour, so layers never
// depend on colour alone), how fast it ages, and how a backend item becomes a globe marker.

export const SHAPES = { circle: 0, diamond: 1, triangle: 2, square: 3, dart: 4, plus: 5, hexagon: 6, ring: 7 }

const H = 3600_000

/**
 * fadeAfter: age (ms) at which a marker has faded to its floor opacity; null = never fades
 * (ongoing situations, live objects). Old events stay visible but recede into the background.
 */
export const LAYERS = [
  { id: 'quakes', label: 'Earthquakes', shape: 'circle', cssVar: '--ui-orange', fallbackVar: '--ui-red', fadeAfter: 48 * H, size: [5, 15],
    hint: 'USGS: magnitude 2.5+ in the last day, 4.5+ in the last week' },
  { id: 'disasters', label: 'Disaster alerts', shape: 'diamond', cssVar: '--ui-red', fallbackVar: '--ui-orange', fadeAfter: 7 * 24 * H, size: [7, 16],
    hint: 'GDACS: earthquakes, cyclones, floods, volcanoes, droughts and wildfires with humanitarian impact' },
  { id: 'natural', label: 'Natural events', shape: 'triangle', cssVar: '--ui-green', fallbackVar: '--ui-accent', fadeAfter: 21 * 24 * H, size: [4, 8],
    hint: 'NASA EONET: wildfires, storms, volcanoes, floods and ice events observed in the last 30 days' },
  { id: 'weather', label: 'Severe weather (US)', shape: 'square', cssVar: '--ui-blue', fallbackVar: '--ui-accent', fadeAfter: null, size: [5, 9],
    hint: 'US National Weather Service extreme and severe alerts' },
  { id: 'incidents', label: 'News incidents', shape: 'hexagon', cssVar: '--ui-yellow', fallbackVar: '--ui-orange', fadeAfter: 3 * H, size: [5, 13],
    hint: 'GDELT: protests, fighting, assaults and threats reported in the news, geolocated, last 3 hours' },
  { id: 'aircraft', label: 'Aircraft', shape: 'dart', cssVar: '--ui-cyan', fallbackVar: '--ui-blue', fadeAfter: null, size: [6, 8],
    hint: 'adsb.lol: military aircraft worldwide, plus all traffic near the centre of the view when zoomed in' },
  { id: 'satellites', label: 'Satellites', shape: 'plus', cssVar: '--ui-purple', fallbackVar: '--ui-accent', fadeAfter: null, size: [5, 5],
    hint: 'CelesTrak orbital elements, positions computed live (SGP4)' },
  { id: 'chokepoints', label: 'Chokepoints', shape: 'ring', cssVar: '--ui-accent', fallbackVar: '--ui-text-primary', fadeAfter: null, size: [9, 9],
    hint: 'Strategic straits and canals for shipping' }
]

export const LAYER_INDEX = Object.fromEntries(LAYERS.map((l, i) => [l.id, i]))
export const layerOf = id => LAYERS[LAYER_INDEX[id]]

const FADE_FLOOR = 0.28

/** Opacity for an item of a given age. */
export function ageAlpha(layer, time, now) {
  if (!layer.fadeAfter || !time) return 1
  const age = Math.max(0, now - time)
  return Math.max(FADE_FLOOR, 1 - (age / layer.fadeAfter) * (1 - FADE_FLOOR))
}

const COUNTRY = /^[A-Z]{2}$/

/** Defensive parse of one backend item; null when it can't be placed or named. */
export function toItem(raw, layerId) {
  if (!raw || typeof raw !== 'object' || typeof raw.id !== 'string') return null
  const num = (v, lo, hi) => (typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi ? v : null)
  const lat = num(raw.lat, -90, 90)
  const lon = num(raw.lon, -180, 180)
  const omm = raw.omm && typeof raw.omm === 'object' ? raw.omm : null
  if ((lat === null || lon === null) && !omm) return null
  const text = (v, n) => (typeof v === 'string' && v.trim() ? v.replace(/\s+/g, ' ').trim().slice(0, n) : null)
  const props = Array.isArray(raw.props)
    ? raw.props
        .filter(r => Array.isArray(r) && r.length === 2)
        .slice(0, 10)
        .map(([k, v]) => [String(k).slice(0, 40), String(v).slice(0, 160)])
    : []
  return {
    id: raw.id.slice(0, 160),
    layer: layerId,
    kind: text(raw.kind, 40) ?? layerId,
    lat,
    lon,
    altKm: num(raw.alt_km, -1, 100000),
    title: text(raw.title, 200) ?? 'Untitled',
    subtitle: text(raw.subtitle, 200),
    severity: num(raw.severity, 0, 1) ?? 0.3,
    time: num(raw.time, 0, 8.64e15),
    country: typeof raw.country === 'string' && COUNTRY.test(raw.country) ? raw.country : null,
    url: typeof raw.url === 'string' && /^https?:\/\//.test(raw.url) ? raw.url : null,
    heading: num(raw.heading, 0, 360),
    speedKts: num(raw.speed_kts, 0, 5000),
    count: num(raw.count, 1, 1e9),
    props,
    omm
  }
}

/** Marker for the globe (null when the item has no position yet). */
export function toMarker(item, now) {
  if (item.lat === null || item.lon === null) return null
  const index = LAYER_INDEX[item.layer]
  const layer = LAYERS[index]
  const [lo, hi] = layer.size
  let alpha = ageAlpha(layer, item.time, now)
  let size = lo + (hi - lo) * item.severity
  if (item.layer === 'satellites') alpha *= item.altKm > 2000 ? 0.45 : 0.8 // high orbits recede
  if (item.layer === 'aircraft' && item.kind !== 'military') {
    alpha *= 0.62
    size *= 0.85
  }
  return {
    id: item.id,
    lat: item.lat,
    lon: item.lon,
    altKm: item.altKm ?? 0,
    shape: SHAPES[layer.shape],
    color: index,
    size,
    heading: item.heading ?? 0,
    alpha,
    layer: index
  }
}
