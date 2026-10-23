// Country lookup over the baked raster: index <-> ISO alpha-2, names, label points, and
// lat/lon -> country. Decoding is lazy so importing this module costs nothing until a globe mounts.
import { COUNTRY_ROWS, LAND_UNASSIGNED, RASTER_HEIGHT, RASTER_RLE, RASTER_WIDTH } from './world.generated.js'

export { LAND_UNASSIGNED, RASTER_HEIGHT, RASTER_WIDTH }

export const COUNTRIES = COUNTRY_ROWS.map(([code, name, lat, lon, area], i) => ({
  index: i + 1,
  code,
  name,
  lat,
  lon,
  area
}))

const byCode = new Map(COUNTRIES.map(c => [c.code, c]))

let displayNames = null
try {
  displayNames = new Intl.DisplayNames(['en'], { type: 'region', fallback: 'none' })
} catch {
  displayNames = null
}

/** Country record for an alpha-2 code, or undefined (codes outside Natural Earth, e.g. tiny states). */
export function countryByCode(code) {
  return code ? byCode.get(code) : undefined
}

export function countryByIndex(index) {
  return index > 0 && index <= COUNTRIES.length ? COUNTRIES[index - 1] : undefined
}

/** English short name for an alpha-2 code, falling back to Natural Earth's name, then the code. */
export function countryName(code) {
  if (!code) return 'Unknown'
  let name = null
  try {
    name = displayNames?.of(code) ?? null
  } catch {
    name = null
  }
  return name || byCode.get(code)?.name || code
}

let raster = null

/** The decoded index raster (row 0 = 90°N, column 0 = 180°W). */
export function countryRaster() {
  if (raster) return raster
  const bytes = decodeBase64(RASTER_RLE)
  const out = new Uint8Array(RASTER_WIDTH * RASTER_HEIGHT)
  let o = 0
  let i = 0
  while (i < bytes.length && o < out.length) {
    const value = bytes[i++]
    let run = 0
    let shift = 0
    let b
    do {
      b = bytes[i++]
      run |= (b & 0x7f) << shift
      shift += 7
    } while (b & 0x80)
    out.fill(value, o, o + run)
    o += run
  }
  raster = out
  return raster
}

function decodeBase64(text) {
  if (typeof atob === 'function') {
    const binary = atob(text)
    const out = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i)
    return out
  }
  return new Uint8Array(Buffer.from(text, 'base64'))
}

/** Raster index under a lat/lon (0 = ocean, LAND_UNASSIGNED = land with no country). */
export function indexAt(lat, lon) {
  const r = countryRaster()
  const x = Math.min(RASTER_WIDTH - 1, Math.max(0, Math.floor(((lon + 180) / 360) * RASTER_WIDTH)))
  const y = Math.min(RASTER_HEIGHT - 1, Math.max(0, Math.floor(((90 - lat) / 180) * RASTER_HEIGHT)))
  return r[y * RASTER_WIDTH + x]
}

export function countryAt(lat, lon) {
  return countryByIndex(indexAt(lat, lon))
}
