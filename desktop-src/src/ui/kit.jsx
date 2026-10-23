// Small presentational pieces: category glyphs, rolling numbers, charts, formatting, and hooks.
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { LAYERS, LAYER_INDEX } from '../data/layers.js'

const nf = new Intl.NumberFormat('en')
const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 })
export const fmt = n => (Number.isFinite(n) ? nf.format(Math.round(n)) : '—')
export const fmtCompact = n => (Number.isFinite(n) ? (Math.abs(n) < 10_000 ? nf.format(Math.round(n)) : compact.format(n)) : '—')
export const pct = (part, whole) => (whole > 0 ? Math.round((part / whole) * 100) : 0)

export function age(ms, now) {
  const s = Math.max(0, Math.round((now - ms) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h`
}

export const clock = ms =>
  new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })

export const layerVar = index => `var(--hom-l${index})`

/** Layer glyph: the same shape the globe draws for that layer, in its theme colour. */
export function Shape({ layer, size = 11, title }) {
  const index = LAYER_INDEX[layer] ?? 0
  const def = LAYERS[index]
  const fill = layerVar(index)
  const s = size
  const c = s / 2
  let path
  switch (def.shape) {
    case 'diamond':
      path = <path d={`M${c} 0.5 L${s - 0.5} ${c} L${c} ${s - 0.5} L0.5 ${c}Z`} />
      break
    case 'triangle':
      path = <path d={`M${c} 0.8 L${s - 0.6} ${s - 1.2} L0.6 ${s - 1.2}Z`} />
      break
    case 'square':
      path = <rect x="1.2" y="1.2" width={s - 2.4} height={s - 2.4} rx="1" />
      break
    case 'dart':
      path = <path d={`M${c} 0.5 L${c + s * 0.3} ${s - 1} L${c} ${s * 0.72} L${c - s * 0.3} ${s - 1}Z`} />
      break
    case 'plus':
      path = <path d={`M${c - 1.3} 0.8h2.6v${c - 2.1}h${c - 2.1}v2.6h-${c - 2.1}v${c - 2.1}h-2.6v-${c - 2.1}h-${c - 2.1}v-2.6h${c - 2.1}Z`} />
      break
    case 'hexagon': {
      const r = c - 0.6
      const pts = Array.from({ length: 6 }, (_, i) => {
        const a = (Math.PI / 3) * i + Math.PI / 6
        return `${(c + r * Math.cos(a)).toFixed(2)},${(c + r * Math.sin(a)).toFixed(2)}`
      }).join(' ')
      path = <polygon points={pts} />
      break
    }
    case 'ring':
      path = <circle cx={c} cy={c} r={c - 1.6} fill="none" stroke={fill} strokeWidth="2" />
      break
    default:
      path = <circle cx={c} cy={c} r={c - 1} />
  }
  return (
    <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} role="img" aria-label={title ?? def.label} style={{ fill, flex: 'none', display: 'block' }}>
      {path}
    </svg>
  )
}

/** Digits that roll into place. Falls back to plain text when motion is reduced. */
export function RollingNumber({ value, format = fmt, reduced = false, className = '' }) {
  const text = format(value)
  if (reduced) return <span className={`htm-num ${className}`}>{text}</span>
  return (
    <span className={`htm-roll htm-num ${className}`} aria-label={text} role="text">
      {[...text].map((ch, i) => {
        const key = `${text.length - i}`
        if (ch < '0' || ch > '9') {
          return (
            <span key={key + ch} aria-hidden="true">
              {ch}
            </span>
          )
        }
        return (
          <span key={key} className="htm-roll-digit" aria-hidden="true">
            <span className="htm-roll-strip" style={{ transform: `translateY(${-Number(ch)}em)` }}>
              {DIGITS.map(d => (
                <span key={d}>{d}</span>
              ))}
            </span>
          </span>
        )
      })}
    </span>
  )
}
const DIGITS = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']

/** Stacked per-layer bars over time (oldest left). */
export function TimelineBars({ buckets, height = 40, label }) {
  const max = Math.max(1, ...buckets.map(b => b.total))
  const w = 100 / buckets.length
  return (
    <svg className="htm-chart" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" height={height} role="img" aria-label={label}>
      <line x1="0" x2="100" y1={height - 0.5} y2={height - 0.5} stroke="var(--htm-line)" strokeWidth="0.5" vectorEffect="non-scaling-stroke" />
      {buckets.map((b, i) => {
        let y = height - 1
        return b.byType.map((v, t) => {
          if (!v) return null
          const h = Math.max(0.8, (v / max) * (height - 4))
          y -= h
          return <rect key={`${i}-${t}`} x={i * w + w * 0.14} width={w * 0.72} y={y} height={h} fill={layerVar(t)} opacity={i === buckets.length - 1 ? 0.45 : 0.85} />
        })
      })}
    </svg>
  )
}

/** Simple bars for a numeric series (oldest left). */
export function SeriesBars({ values, height = 26, highlightLast = true, label }) {
  const max = Math.max(1, ...values)
  const w = 100 / Math.max(1, values.length)
  return (
    <svg className="htm-chart" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" height={height} role="img" aria-label={label}>
      {values.map((v, i) => {
        const h = Math.max(1, (v / max) * height)
        return (
          <rect
            key={i}
            x={i * w + w * 0.18}
            width={w * 0.64}
            y={height - h}
            height={h}
            fill="var(--ui-text-secondary)"
            opacity={highlightLast && i === values.length - 1 ? 0.9 : 0.32}
          />
        )
      })}
    </svg>
  )
}

/** Area chart with a gradient, min/max labels and first/last date. */
export function AreaChart({ values, dates, height = 74, label, color = 'var(--ui-accent)' }) {
  const id = useMemo(() => `htm-g${Math.random().toString(36).slice(2, 8)}`, [])
  if (!values?.length) return null
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = max - min || 1
  const W = 300
  const top = 8
  const usable = height - top - 14
  const pts = values.map((v, i) => [(i / Math.max(1, values.length - 1)) * W, top + (1 - (v - min) / span) * usable])
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
  const area = `${line} L${W} ${top + usable} L0 ${top + usable}Z`
  const [lx, ly] = pts[pts.length - 1]
  return (
    <svg className="htm-chart" viewBox={`0 0 ${W} ${height}`} height={height} role="img" aria-label={label}>
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.35" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.4" strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r="2.6" fill={color} />
      {dates?.length ? (
        <>
          <text x="0" y={height - 2}>{shortDate(dates[0])}</text>
          <text x={W} y={height - 2} textAnchor="end">{shortDate(dates[dates.length - 1])}</text>
        </>
      ) : null}
    </svg>
  )
}

export function MiniLine({ values, height = 22, color = 'var(--ui-text-secondary)' }) {
  if (!values?.length) return null
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = max - min || 1
  const W = 100
  const d = values
    .map((v, i) => `${i ? 'L' : 'M'}${((i / Math.max(1, values.length - 1)) * W).toFixed(1)} ${(2 + (1 - (v - min) / span) * (height - 4)).toFixed(1)}`)
    .join(' ')
  return (
    <svg className="htm-chart" viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" height={height} aria-hidden="true">
      <path d={d} fill="none" stroke={color} strokeWidth="1.2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  )
}

function shortDate(iso) {
  const d = new Date(`${iso}T00:00:00Z`)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('en', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

/** Per-category share bar. */
export function StackBar({ byType, height = 4 }) {
  const total = byType.reduce((a, b) => a + b, 0)
  return (
    <div className="htm-stack" style={{ height }}>
      {byType.map((v, i) => (v ? <i key={i} style={{ width: `${(v / total) * 100}%`, background: layerVar(i) }} /> : null))}
    </div>
  )
}

/** Re-render at most every `ms` when an external store changes (bursts stay cheap). */
export function useThrottled(subscribe, getVersion, ms = 250) {
  const ref = useRef({ version: getVersion(), timer: 0 })
  const snapshot = useSyncExternalStore(
    notify =>
      subscribe(() => {
        if (ref.current.timer) return
        ref.current.timer = setTimeout(() => {
          ref.current.timer = 0
          ref.current.version = getVersion()
          notify()
        }, ms)
      }),
    () => ref.current.version
  )
  useEffect(() => () => clearTimeout(ref.current.timer), [])
  return snapshot
}

/** A ticking timestamp for relative times; pauses with the page. */
export function useNow(ms = 1000, active = true) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return undefined
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms, active])
  return now
}

/** Whether the user asked for reduced motion. */
export function usePrefersReducedMotion() {
  const query = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null
  return useSyncExternalStore(
    notify => {
      query?.addEventListener('change', notify)
      return () => query?.removeEventListener('change', notify)
    },
    () => !!query?.matches
  )
}
