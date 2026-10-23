// Bakes Natural Earth 1:50m countries (world-atlas, public domain) into src/geo/world.generated.js:
// an equirectangular country-index raster, run-length encoded, plus one row per country with its
// ISO alpha-2 code, name, label point and area. The globe shader reads the raster as a texture, so
// the plugin ships no polygon library and does no geometry work at runtime.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { feature } from 'topojson-client'
import { geoArea, geoCentroid } from 'd3-geo'

const require = createRequire(import.meta.url)
const here = dirname(fileURLToPath(import.meta.url))
const W = 4096
const H = 2048

const topo = JSON.parse(readFileSync(require.resolve('world-atlas/countries-50m.json'), 'utf8'))
const codes = require('i18n-iso-countries/codes.json')
const alpha2ByNumeric = new Map(codes.map(([a2, , num]) => [num, a2]))
// Features world-atlas ships without an ISO numeric id.
const unnumbered = { Somaliland: 'SO', Kosovo: 'XK', 'N. Cyprus': 'CY', 'Indian Ocean Ter.': 'AU', 'Siachen Glacier': null }

const countries = feature(topo, topo.objects.countries).features
const byCode = new Map()
const unassigned = []
for (const f of countries) {
  const name = f.properties.name
  const code = f.id ? alpha2ByNumeric.get(f.id) ?? null : unnumbered[name] ?? null
  if (!code) {
    unassigned.push(f)
    continue
  }
  if (!byCode.has(code)) byCode.set(code, { code, name, features: [] })
  byCode.get(code).features.push(f)
}

const entries = [...byCode.values()].sort((a, b) => a.code.localeCompare(b.code))
if (entries.length > 254) throw new Error(`too many countries for an 8-bit index: ${entries.length}`)
const LAND_UNASSIGNED = 255

const raster = new Uint8Array(W * H)

function polygonsOf(geometry) {
  if (geometry.type === 'Polygon') return [geometry.coordinates]
  if (geometry.type === 'MultiPolygon') return geometry.coordinates
  return []
}

// Make a ring's longitudes continuous across the antimeridian. A ring that circles a pole
// (Antarctica) drifts by 360 degrees; close it through that pole so it stays a simple polygon.
function unwrap(ring) {
  const out = [[ring[0][0], ring[0][1]]]
  let shift = 0
  let latSum = ring[0][1]
  for (let i = 1; i < ring.length; i++) {
    const prev = ring[i - 1][0]
    const lon = ring[i][0]
    if (lon - prev > 180) shift -= 360
    else if (prev - lon > 180) shift += 360
    out.push([lon + shift, ring[i][1]])
    latSum += ring[i][1]
  }
  const drift = out[out.length - 1][0] - out[0][0]
  if (Math.abs(drift) > 180) {
    const pole = latSum / ring.length < 0 ? -90 : 90
    out.push([out[out.length - 1][0], pole], [out[0][0], pole], [out[0][0], out[0][1]])
  }
  return out
}

// Even-odd scanline fill of one polygon (outer ring + holes) at texel centres, drawn at -360/0/+360
// so unwrapped rings that spill past the antimeridian land on the other edge of the raster.
function fillPolygon(rawRings, value) {
  const rings = rawRings.map(unwrap)
  for (const offset of [-360, 0, 360]) fillShifted(rings, value, offset)
}

function fillShifted(rings, value, offset) {
  let minLon = Infinity
  let maxLon = -Infinity
  for (const ring of rings) for (const [lon] of ring) {
    if (lon + offset < minLon) minLon = lon + offset
    if (lon + offset > maxLon) maxLon = lon + offset
  }
  if (maxLon < -180 || minLon > 180) return
  let minLat = 90
  let maxLat = -90
  for (const ring of rings) for (const [, lat] of ring) {
    if (lat < minLat) minLat = lat
    if (lat > maxLat) maxLat = lat
  }
  const y0 = Math.max(0, Math.floor(((90 - maxLat) / 180) * H))
  const y1 = Math.min(H - 1, Math.ceil(((90 - minLat) / 180) * H))
  const xs = []
  for (let y = y0; y <= y1; y++) {
    const lat = 90 - ((y + 0.5) * 180) / H
    xs.length = 0
    for (const ring of rings) {
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [lonA, latA] = ring[j]
        const [lonB, latB] = ring[i]
        if ((latA > lat) === (latB > lat)) continue
        const lon = offset + lonA + ((lat - latA) / (latB - latA)) * (lonB - lonA)
        xs.push(((lon + 180) / 360) * W)
      }
    }
    xs.sort((a, b) => a - b)
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const from = Math.max(0, Math.ceil(xs[k] - 0.5))
      const to = Math.min(W - 1, Math.floor(xs[k + 1] - 0.5))
      for (let x = from; x <= to; x++) raster[y * W + x] = value
    }
  }
}

for (const f of unassigned) for (const p of polygonsOf(f.geometry)) fillPolygon(p, LAND_UNASSIGNED)

const table = entries.map((entry, i) => {
  const index = i + 1
  let largest = null
  let largestArea = -1
  let area = 0
  for (const f of entry.features) {
    for (const p of polygonsOf(f.geometry)) {
      fillPolygon(p, index)
      const poly = { type: 'Polygon', coordinates: p }
      const a = geoArea(poly)
      area += a
      if (a > largestArea) {
        largestArea = a
        largest = poly
      }
    }
  }
  const [lon, lat] = geoCentroid(largest)
  return [entry.code, entry.name, +lat.toFixed(3), +lon.toFixed(3), +area.toExponential(3)]
})

// Run-length encode row-major: (value byte, varint run length) pairs.
const out = []
let i = 0
while (i < raster.length) {
  const value = raster[i]
  let run = 1
  while (i + run < raster.length && raster[i + run] === value) run++
  out.push(value)
  let n = run
  while (n >= 0x80) {
    out.push((n & 0x7f) | 0x80)
    n >>>= 7
  }
  out.push(n)
  i += run
}
const data = Buffer.from(out).toString('base64')

const target = join(here, '..', 'src', 'geo', 'world.generated.js')
mkdirSync(dirname(target), { recursive: true })
writeFileSync(
  target,
  `// Generated by scripts/build-geo.mjs from world-atlas countries-50m (Natural Earth, public domain). Do not edit.\n` +
    `export const RASTER_WIDTH = ${W}\nexport const RASTER_HEIGHT = ${H}\nexport const LAND_UNASSIGNED = ${LAND_UNASSIGNED}\n` +
    `// [alpha-2, Natural Earth name, label lat, label lon, area in steradians]; row i is raster index i + 1.\n` +
    `export const COUNTRY_ROWS = ${JSON.stringify(table)}\n` +
    `export const RASTER_RLE = '${data}'\n`
)
console.log(`${table.length} countries, raster ${W}x${H}, RLE ${out.length} bytes (${data.length} base64)`)
