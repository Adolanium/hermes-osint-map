// A small dithered globe for chat cards, drawn once on a 2D canvas. A transcript can hold many
// cards and every WebGL context counts against a low per-page limit, so cards never use three.js:
// they project the same country raster the big globe uses (orthographic, centred on the area),
// shade it with the same ordered dither, and draw each layer's shape on top.
import { LAYER_INDEX, LAYERS, SHAPES } from '../data/layers.js'
import { countryRaster, RASTER_HEIGHT, RASTER_WIDTH } from '../geo/world.js'

const DEG = Math.PI / 180
const EARTH_KM = 6371
// 4x4 Bayer matrix, the same ordered-dither pattern the big globe's shader uses.
const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map(v => (v + 0.5) / 16)

const to255 = c => c.map(v => Math.round(Math.max(0, Math.min(1, v)) * 255))
const css = c => `rgb(${to255(c).join(' ')})`

/** Project lat/lon onto the visible hemisphere; null when it is on the far side. */
export function project(lat, lon, lat0, lon0) {
  const phi = lat * DEG
  const lam = (lon - lon0) * DEG
  const phi0 = lat0 * DEG
  const cosc = Math.sin(phi0) * Math.sin(phi) + Math.cos(phi0) * Math.cos(phi) * Math.cos(lam)
  if (cosc < 0) return null
  return {
    x: Math.cos(phi) * Math.sin(lam),
    y: Math.cos(phi0) * Math.sin(phi) - Math.sin(phi0) * Math.cos(phi) * Math.cos(lam),
    depth: cosc
  }
}

/** Inverse orthographic: disc coordinates (-1..1, y up) back to lat/lon. */
function unproject(x, y, lat0, lon0) {
  const rho = Math.hypot(x, y)
  if (rho > 1) return null
  const c = Math.asin(rho)
  const phi0 = lat0 * DEG
  const sinc = Math.sin(c)
  const cosc = Math.cos(c)
  const lat = rho === 0 ? lat0 : Math.asin(cosc * Math.sin(phi0) + (y * sinc * Math.cos(phi0)) / rho) / DEG
  const lon = lon0 + Math.atan2(x * sinc, rho * Math.cos(phi0) * cosc - y * Math.sin(phi0) * sinc) / DEG
  return { lat, lon: ((((lon + 180) % 360) + 360) % 360) - 180, cosc }
}

/** Points on the small circle `km` around a centre, for the area ring. */
export function ringPoints(lat, lon, km, steps = 96) {
  const d = km / EARTH_KM
  const phi1 = lat * DEG
  const lam1 = lon * DEG
  const out = []
  for (let i = 0; i <= steps; i++) {
    const brng = (i / steps) * 2 * Math.PI
    const phi2 = Math.asin(Math.sin(phi1) * Math.cos(d) + Math.cos(phi1) * Math.sin(d) * Math.cos(brng))
    const lam2 = lam1 + Math.atan2(Math.sin(brng) * Math.sin(d) * Math.cos(phi1), Math.cos(d) - Math.sin(phi1) * Math.sin(phi2))
    out.push({ lat: phi2 / DEG, lon: lam2 / DEG })
  }
  return out
}

function drawShape(g, shape, x, y, r, heading) {
  g.beginPath()
  switch (shape) {
    case SHAPES.diamond:
      g.moveTo(x, y - r); g.lineTo(x + r, y); g.lineTo(x, y + r); g.lineTo(x - r, y); g.closePath(); break
    case SHAPES.triangle:
      g.moveTo(x, y - r); g.lineTo(x + r * 0.9, y + r * 0.75); g.lineTo(x - r * 0.9, y + r * 0.75); g.closePath(); break
    case SHAPES.square:
    case SHAPES.frame:
      g.rect(x - r * 0.8, y - r * 0.8, r * 1.6, r * 1.6); break
    case SHAPES.dart: {
      const a = ((heading ?? 0) - 90) * DEG
      const p = (ang, len) => [x + Math.cos(a + ang) * len, y + Math.sin(a + ang) * len]
      g.moveTo(...p(0, r * 1.2)); g.lineTo(...p(2.5, r)); g.lineTo(...p(Math.PI, r * 0.35)); g.lineTo(...p(-2.5, r)); g.closePath(); break
    }
    case SHAPES.plus:
      g.rect(x - r, y - r * 0.28, r * 2, r * 0.56); g.rect(x - r * 0.28, y - r, r * 0.56, r * 2); break
    case SHAPES.hexagon:
      for (let i = 0; i < 6; i++) {
        const ang = (Math.PI / 3) * i + Math.PI / 6
        const px = x + Math.cos(ang) * r
        const py = y + Math.sin(ang) * r
        if (i) g.lineTo(px, py)
        else g.moveTo(px, py)
      }
      g.closePath(); break
    default:
      g.arc(x, y, r * 0.85, 0, Math.PI * 2)
  }
}

/**
 * Draw the card globe. `items` are backend items with lat/lon/layer/severity; `focus` (an item id)
 * is drawn on top with a halo. Returns the screen position of every drawn item for hit testing.
 */
/** How much of the disc to show: 1 = the whole hemisphere, smaller zooms in on the area. */
export function spanFor(radiusKm) {
  if (!radiusKm) return 1
  return Math.min(1, Math.max(0.06, Math.sin(Math.min(Math.PI / 2, (radiusKm / EARTH_KM) * 1.7))))
}

export function drawMiniGlobe(canvas, { lat0, lon0, radiusKm, span = spanFor(radiusKm), items, palette, focus = null, dither = true }) {
  const dpr = Math.min(2, window.devicePixelRatio || 1)
  const cssSize = canvas.clientWidth || 200
  const size = Math.round(cssSize * dpr)
  if (canvas.width !== size) canvas.width = canvas.height = size
  const g = canvas.getContext('2d')
  g.clearRect(0, 0, size, size)
  const half = size / 2
  const radius = half - 2 * dpr

  // Sphere: shade land and ocean per pixel, lit from the upper left like the big globe.
  const raster = countryRaster()
  const image = g.createImageData(size, size)
  const data = image.data
  const ocean = to255(palette.ocean)
  const oceanLit = to255(palette.oceanLit)
  const land = to255(palette.land)
  const landLow = to255(palette.landLow)
  // Pass 1: the raster index under every pixel (-1 off the sphere) and how lit it is.
  const index = new Int16Array(size * size).fill(-1)
  const lightAt = new Float32Array(size * size)
  for (let py = 0; py < size; py++) {
    const y = (-(py - half) / radius) * span
    for (let px = 0; px < size; px++) {
      const x = ((px - half) / radius) * span
      const at = unproject(x, y, lat0, lon0)
      if (!at) continue
      const rx = Math.min(RASTER_WIDTH - 1, Math.floor(((at.lon + 180) / 360) * RASTER_WIDTH))
      const ry = Math.min(RASTER_HEIGHT - 1, Math.floor(((90 - at.lat) / 180) * RASTER_HEIGHT))
      index[py * size + px] = raster[ry * RASTER_WIDTH + rx]
      lightAt[py * size + px] = Math.max(0, Math.min(1, 0.35 + 0.65 * at.cosc - 0.18 * x + 0.12 * y))
    }
  }
  // Pass 2: dithered land with bright coasts and borders, flat ocean with a lit rim on the full disc.
  const border = Math.max(1, Math.round(dpr))
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      const i = py * size + px
      const idx = index[i]
      if (idx < 0) continue
      const light = lightAt[i]
      const threshold = dither ? BAYER[(py & 3) * 4 + (px & 3)] : 0.5
      let c
      if (idx !== 0) {
        const right = px + border < size ? index[i + border] : idx
        const down = py + border < size ? index[i + border * size] : idx
        const edge = (right >= 0 && right !== idx) || (down >= 0 && down !== idx)
        // Land texture: a fixed dither density, a little brighter toward the light.
        c = edge ? land : 0.3 + 0.28 * light > threshold ? land : landLow
      } else {
        c = span > 0.97 && light > 0.86 && light > threshold * 0.6 + 0.62 ? oceanLit : ocean
      }
      const o = i * 4
      data[o] = c[0]
      data[o + 1] = c[1]
      data[o + 2] = c[2]
      data[o + 3] = 255
    }
  }
  g.putImageData(image, 0, 0)

  // Rim, when the whole disc is in view.
  if (span > 0.97) {
    g.beginPath()
    g.arc(half, half, radius, 0, Math.PI * 2)
    g.strokeStyle = css(palette.rim)
    g.globalAlpha = 0.55
    g.lineWidth = 1.2 * dpr
    g.stroke()
    g.globalAlpha = 1
  }

  const toScreen = (lat, lon) => {
    const p = project(lat, lon, lat0, lon0)
    return p ? { x: half + (p.x / span) * radius, y: half - (p.y / span) * radius, depth: p.depth } : null
  }

  // The watched area.
  if (radiusKm) {
    g.beginPath()
    let pen = false
    for (const pt of ringPoints(lat0, lon0, radiusKm)) {
      const s = toScreen(pt.lat, pt.lon)
      if (!s) {
        pen = false
        continue
      }
      if (pen) g.lineTo(s.x, s.y)
      else g.moveTo(s.x, s.y)
      pen = true
    }
    g.setLineDash([4 * dpr, 3 * dpr])
    g.strokeStyle = css(palette.accent)
    g.lineWidth = 1.4 * dpr
    g.stroke()
    g.setLineDash([])
  }

  // Markers, least severe first so the important ones sit on top.
  const placed = []
  const ordered = [...items].filter(i => i.lat !== null && i.lon !== null).sort((a, b) => (a.id === focus) - (b.id === focus) || a.severity - b.severity)
  for (const item of ordered) {
    const s = toScreen(item.lat, item.lon)
    if (!s || s.x < -8 || s.y < -8 || s.x > size + 8 || s.y > size + 8) continue
    const index = LAYER_INDEX[item.layer]
    const layer = LAYERS[index]
    if (!layer) continue
    const [lo, hi] = layer.size
    const r = ((lo + (hi - lo) * (item.severity ?? 0.3)) / 2) * dpr * (span < 0.97 ? 1.45 : 0.9)
    const color = css(palette.layers[index])
    if (item.id === focus) {
      g.beginPath()
      g.arc(s.x, s.y, r + 5 * dpr, 0, Math.PI * 2)
      g.strokeStyle = color
      g.lineWidth = 1.5 * dpr
      g.stroke()
    }
    drawShape(g, SHAPES[layer.shape], s.x, s.y, r, item.heading)
    g.globalAlpha = 0.35 + 0.65 * s.depth
    if (layer.shape === 'ring' || layer.shape === 'frame') {
      g.strokeStyle = color
      g.lineWidth = 1.4 * dpr
      g.stroke()
    } else {
      g.fillStyle = color
      g.fill()
      g.strokeStyle = css(palette.ocean)
      g.lineWidth = 0.8 * dpr
      g.stroke()
    }
    g.globalAlpha = 1
    placed.push({ id: item.id, x: s.x / dpr, y: s.y / dpr, r: Math.max(6, r / dpr + 3) })
  }

  // Centre cross.
  if (radiusKm) {
    g.strokeStyle = css(palette.ink)
    g.globalAlpha = 0.7
    g.lineWidth = 1 * dpr
    g.beginPath()
    g.moveTo(half - 5 * dpr, half); g.lineTo(half + 5 * dpr, half)
    g.moveTo(half, half - 5 * dpr); g.lineTo(half, half + 5 * dpr)
    g.stroke()
    g.globalAlpha = 1
  }
  return placed
}
