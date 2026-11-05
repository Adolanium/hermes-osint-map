// Derives the globe's colours from the active Hermes theme's CSS variables, so the globe reskins
// with every theme. No colour here is hardcoded: each one is a theme variable or a mix of two.
import { CATEGORIES } from '../data/cyber.js'
import { LAYERS } from '../data/layers.js'

/** Resolve CSS colours (any syntax the browser accepts: hex, rgb, oklch, color-mix…) to sRGB 0..1. */
export function createColorResolver(host) {
  const probe = document.createElement('span')
  probe.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden;pointer-events:none;visibility:hidden'
  host.appendChild(probe)
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  return {
    resolve(expression) {
      probe.style.color = ''
      probe.style.color = expression
      const computed = getComputedStyle(probe).color
      ctx.clearRect(0, 0, 1, 1)
      ctx.fillStyle = computed
      ctx.fillRect(0, 0, 1, 1)
      const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data
      return a === 0 ? null : [r / 255, g / 255, b / 255]
    },
    dispose() {
      probe.remove()
    }
  }
}

const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
const luminance = c => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

export function buildPalette(resolver) {
  const read = (...vars) => {
    for (const v of vars) {
      const c = resolver.resolve(`var(${v})`)
      if (c) return c
    }
    return null
  }
  const text = read('--ui-text-primary') ?? resolver.resolve('CanvasText') ?? [0.9, 0.9, 0.9]
  const bg = read('--ui-bg-editor', '--ui-chat-surface-background', '--ui-surface-background', '--ui-base') ??
    resolver.resolve('Canvas') ?? [0.06, 0.06, 0.06]
  const accent = read('--ui-accent', '--ui-blue') ?? text
  const red = read('--ui-red', '--ui-orange') ?? accent
  const dark = luminance(bg) < luminance(text)
  const layers = LAYERS.map(l => {
    const color = read(l.cssVar, l.fallbackVar) ?? accent
    // On light themes pull layer colours slightly toward the ink so small markers keep contrast.
    return dark ? color : mix(color, text, 0.18)
  })
  const cyber = CATEGORIES.map(c => {
    const color = read(c.cssVar, c.fallbackVar) ?? accent
    return dark ? color : mix(color, text, 0.18)
  })
  return {
    dark,
    ocean: mix(bg, text, dark ? 0.035 : 0.045),
    oceanLit: mix(mix(bg, accent, 0.5), text, dark ? 0.12 : 0.05),
    landLow: mix(bg, text, dark ? 0.16 : 0.2),
    land: mix(bg, text, dark ? 0.74 : 0.62),
    accent,
    heat: red,
    rim: mix(accent, text, dark ? 0.18 : 0.05),
    star: mix(bg, text, 0.85),
    ink: text,
    layers,
    cyber
  }
}

// --- Vision modes ------------------------------------------------------------------------------
// Night vision and thermal remap every globe colour through one ramp: the map's ground by its
// brightness (low on the ramp), events high on it, so markers and arcs stay the hottest things.
// CRT keeps the theme's colours; its look is the overlay (scanlines and vignette) on the page.

export const VISION_MODES = [
  { id: 'normal', label: 'Normal' },
  { id: 'nvg', label: 'Night vision' },
  { id: 'thermal', label: 'Thermal' },
  { id: 'crt', label: 'CRT' }
]

const RAMPS = {
  nvg: [[0, [0.0, 0.03, 0.01]], [0.3, [0.03, 0.2, 0.06]], [0.62, [0.18, 0.78, 0.3]], [0.85, [0.55, 1.0, 0.55]], [1, [0.9, 1.0, 0.85]]],
  thermal: [[0, [0.02, 0.0, 0.07]], [0.28, [0.2, 0.04, 0.42]], [0.5, [0.62, 0.1, 0.48]], [0.72, [0.95, 0.38, 0.12]], [0.88, [1.0, 0.74, 0.2]], [1, [1.0, 0.98, 0.78]]]
}

function ramp(stops, t) {
  t = Math.max(0, Math.min(1, t))
  for (let i = 1; i < stops.length; i++) {
    const [t1, c1] = stops[i]
    const [t0, c0] = stops[i - 1]
    if (t <= t1) return mix(c0, c1, (t - t0) / (t1 - t0))
  }
  return stops[stops.length - 1][1]
}

/** The palette as seen through a vision mode. */
export function applyVision(p, mode) {
  const stops = RAMPS[mode]
  if (!stops) return p
  const ground = c => ramp(stops, 0.04 + luminance(c) * 0.62)
  const hot = (c, k = 0) => ramp(stops, 0.74 + luminance(c) * 0.2 + k)
  return {
    ...p,
    dark: true,
    ocean: ramp(stops, 0.03),
    oceanLit: ramp(stops, 0.16),
    landLow: ground(p.landLow),
    land: ramp(stops, mode === 'thermal' ? 0.46 : 0.5),
    rim: ramp(stops, 0.58),
    star: ramp(stops, 0.7),
    ink: ramp(stops, 0.95),
    accent: hot(p.accent, 0.06),
    heat: ramp(stops, mode === 'thermal' ? 0.97 : 0.9),
    layers: p.layers.map(c => hot(c)),
    cyber: (p.cyber ?? []).map(c => hot(c, 0.05))
  }
}
