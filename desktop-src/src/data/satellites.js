// Satellite positions from CelesTrak orbital elements (OMM JSON), propagated with SGP4 in the page:
// the backend sends elements a few times a day, and positions are computed live.
import { degreesLat, degreesLong, eciToGeodetic, gstime, json2satrec, propagate } from 'satellite.js'

/** Build propagators once per element set. Items without valid elements are dropped. */
export function buildSatellites(items) {
  const out = []
  for (const item of items) {
    if (!item.omm) continue
    try {
      const satrec = json2satrec(item.omm)
      if (satrec && !satrec.error) out.push({ item, satrec })
    } catch {
      // malformed element set: skip it
    }
  }
  return out
}

/** Geodetic position at `date`, or null when SGP4 fails (decayed orbit, bad elements). */
export function positionAt(satrec, date) {
  const pv = propagate(satrec, date)
  if (!pv || !pv.position || typeof pv.position === 'boolean') return null
  const gd = eciToGeodetic(pv.position, gstime(date))
  const lat = degreesLat(gd.latitude)
  const lon = degreesLong(gd.longitude)
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(gd.height)) return null
  const v = pv.velocity
  const speed = v && typeof v !== 'boolean' ? Math.hypot(v.x, v.y, v.z) : null
  return { lat, lon, altKm: gd.height, speedKms: speed }
}

/** Orbital period in minutes from the mean motion (rad/min). */
export function periodMinutes(satrec) {
  return satrec.no > 0 ? (2 * Math.PI) / satrec.no : null
}

/** Track for the next orbit (at most `points` samples). */
export function orbitPath(satrec, from, points = 180) {
  const period = periodMinutes(satrec) ?? 95
  const span = Math.min(period, 24 * 60) * 60_000
  const out = []
  for (let i = 0; i < points; i++) {
    const p = positionAt(satrec, new Date(from.getTime() + (span * i) / (points - 1)))
    if (p) out.push(p)
  }
  return out
}

/** Heading (degrees clockwise from north) between two nearby positions. */
export function bearing(a, b) {
  const toRad = Math.PI / 180
  const y = Math.sin((b.lon - a.lon) * toRad) * Math.cos(b.lat * toRad)
  const x = Math.cos(a.lat * toRad) * Math.sin(b.lat * toRad) - Math.sin(a.lat * toRad) * Math.cos(b.lat * toRad) * Math.cos((b.lon - a.lon) * toRad)
  return ((Math.atan2(y, x) / toRad) + 360) % 360
}
