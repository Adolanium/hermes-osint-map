// The ::osint-map transcript directive: a live map card inside a Hermes chat. The agent writes
// ::osint-map{lat="23.7" lon="121" radius="300"} (or country="JP") after using the osint tools, and
// Desktop draws this card in its place: a small dithered globe of the area, the most severe items,
// and buttons that open the full map there or start a watch. Attributes are model output, so they
// are validated here and the card renders nothing at all when they don't describe a real area.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Codicon, GlyphSpinner, host, useTheme } from '@hermes/plugin-sdk'
import { LAYER_INDEX, LAYERS, toItem } from '../data/layers.js'
import { countryAt, countryByCode, countryName } from '../geo/world.js'
import { buildPalette, createColorResolver } from '../globe/palette.js'
import { age, Shape, useNow } from '../ui/kit.jsx'
import { drawMiniGlobe, spanFor } from './minimap.js'
import { CARD_CSS } from './styles.js'

const PAGE = '/osint'
const EVENT_LAYERS = LAYERS.map(l => l.id).filter(id => id !== 'satellites')
const NUMBER = /^-?\d{1,3}(?:\.\d+)?$/
const EARTH_KM = 6371
const REFRESH_MS = 90_000

/** Directive attributes → a validated area, or null. Exported for tests. */
export function parseAttrs(attrs) {
  if (!attrs || typeof attrs !== 'object') return null
  const str = v => (typeof v === 'string' ? v.trim() : '')
  const out = { layers: EVENT_LAYERS, title: null }
  const code = str(attrs.country).toUpperCase()
  if (code) {
    if (!/^[A-Z]{2}$/.test(code) || !countryByCode(code)) return null
    out.country = code
  }
  const lat = str(attrs.lat)
  const lon = str(attrs.lon)
  if (lat || lon) {
    if (!NUMBER.test(lat) || !NUMBER.test(lon)) return null
    const la = Number(lat)
    const lo = Number(lon)
    if (Math.abs(la) > 90 || Math.abs(lo) > 180) return null
    out.lat = la
    out.lon = lo
    const r = Number(str(attrs.radius) || 500)
    if (!Number.isFinite(r) || r < 1) return null
    out.radiusKm = Math.min(5000, Math.max(10, r))
  }
  if (!out.country && out.lat === undefined) return null
  const layers = str(attrs.layers)
  if (layers) {
    const ids = [...new Set(layers.split(',').map(s => s.trim()).filter(Boolean))]
    if (!ids.length || ids.some(id => !EVENT_LAYERS.includes(id))) return null
    out.layers = ids
  }
  const title = str(attrs.title).replace(/[\u0000-\u001f]/g, '').slice(0, 60)
  out.title = title || null
  return out
}

function query(area) {
  const q = new URLSearchParams()
  if (area.country) q.set('country', area.country)
  if (area.lat !== undefined) {
    q.set('lat', String(area.lat))
    q.set('lon', String(area.lon))
    q.set('radius', String(area.radiusKm))
  }
  if (area.layers !== EVENT_LAYERS) q.set('layers', area.layers.join(','))
  q.set('limit', '120')
  return `/area?${q}`
}

const kmBetween = (a, b) => {
  const r = Math.PI / 180
  const dLat = (b.lat - a.lat) * r
  const dLon = (b.lon - a.lon) * r
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) ** 2
  return 2 * EARTH_KM * Math.asin(Math.min(1, Math.sqrt(h)))
}

/** Where to centre the card and how far to zoom. */
function framing(area, items) {
  if (area.lat !== undefined) return { lat0: area.lat, lon0: area.lon, radiusKm: area.radiusKm, ring: true }
  const c = countryByCode(area.country)
  const centre = { lat: c.lat, lon: c.lon }
  const spread = items.filter(i => i.lat !== null).map(i => kmBetween(centre, i)).sort((a, b) => a - b)
  const p90 = spread.length ? spread[Math.floor(spread.length * 0.9)] : 0
  return { lat0: c.lat, lon0: c.lon, radiusKm: Math.min(3000, Math.max(350, p90 * 1.15 || 800)), ring: false }
}

export function MapCard({ attrs, rest, focus, watchRequest }) {
  const area = useMemo(() => parseAttrs(attrs), [attrs])
  if (!area) return null
  return <Card area={area} rest={rest} focus={focus} watchRequest={watchRequest} />
}

function Card({ area, rest, focus, watchRequest }) {
  const rootRef = useRef(null)
  const canvasRef = useRef(null)
  const placedRef = useRef([])
  const [state, setState] = useState({ loading: true, data: null, error: null })
  const [picked, setPicked] = useState(null)
  const { themeName, resolvedMode } = useThemeSafe()
  const now = useNow(30_000)
  const path = query(area)

  const load = useCallback(async () => {
    try {
      const answer = await rest(path)
      if (answer?.ok) setState({ loading: false, data: answer, error: null })
      else setState(s => ({ loading: false, data: s.data, error: answer?.error ?? 'The map data is unavailable.' }))
    } catch (error) {
      setState(s => ({ loading: false, data: s.data, error: friendly(error) }))
    }
  }, [rest, path])

  const hasData = useRef(false)
  hasData.current = !!state.data
  useEffect(() => {
    let alive = true
    const run = () => alive && load()
    run()
    // Quick retries cover the backend mounting its routes on the first request after a restart.
    const retries = [3000, 8000].map(ms => setTimeout(() => alive && !hasData.current && load(), ms))
    const id = setInterval(run, REFRESH_MS)
    return () => {
      alive = false
      retries.forEach(clearTimeout)
      clearInterval(id)
    }
  }, [load])

  const items = useMemo(() => (state.data?.items ?? []).map(raw => toItem(raw, raw?.layer)).filter(i => i && i.layer in LAYER_INDEX), [state.data])
  const frame = useMemo(() => (area.country || items.length || area.lat !== undefined ? framing(area, items) : null), [area, items])

  useEffect(() => {
    const canvas = canvasRef.current
    const root = rootRef.current
    if (!canvas || !root || !frame) return undefined
    const resolver = createColorResolver(root)
    const id = requestAnimationFrame(() => {
      try {
        placedRef.current = drawMiniGlobe(canvas, {
          lat0: frame.lat0,
          lon0: frame.lon0,
          radiusKm: frame.ring ? frame.radiusKm : null,
          span: spanFor(frame.radiusKm),
          items,
          palette: buildPalette(resolver),
          focus: picked
        })
      } catch (error) {
        console.warn('[hermes-osint-map] card globe failed', error)
      }
    })
    return () => {
      cancelAnimationFrame(id)
      resolver.dispose()
    }
  }, [frame, items, picked, themeName, resolvedMode])

  const onCanvasClick = e => {
    const box = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - box.left
    const y = e.clientY - box.top
    const hit = [...placedRef.current].reverse().find(p => Math.hypot(p.x - x, p.y - y) <= p.r)
    setPicked(hit ? hit.id : null)
  }

  const centreCountry = area.lat !== undefined ? countryAt(area.lat, area.lon)?.code : null
  const title =
    area.title ||
    (area.country ? countryName(area.country) : centreCountry ? `${countryName(centreCountry)} · ${Math.round(area.radiusKm)} km` : `${Math.round(area.radiusKm)} km around ${area.lat.toFixed(1)}, ${area.lon.toFixed(1)}`)
  const counts = state.data?.counts ?? {}
  const top = picked ? [items.find(i => i.id === picked), ...items.filter(i => i.id !== picked)].filter(Boolean).slice(0, 4) : items.slice(0, 4)
  const updated = Object.values(state.data?.status ?? {}).map(s => s?.updated).filter(Boolean)
  const updatedAt = updated.length ? Math.min(...updated) : null
  const target = {
    lat: frame?.lat0 ?? null,
    lon: frame?.lon0 ?? null,
    radiusKm: frame?.radiusKm ?? null,
    country: area.country ?? null,
    label: area.title || (area.country ? countryName(area.country) : null)
  }
  const openMap = () => {
    focus.set({ ...target, item: picked, at: Date.now() })
    host.navigate(PAGE)
  }
  const watch = () => {
    watchRequest.set({ ...target, radiusKm: area.lat !== undefined ? area.radiusKm : null, at: Date.now() })
    host.navigate(PAGE)
  }

  return (
    <div ref={rootRef} className="homc" data-zoomed={frame && spanFor(frame.radiusKm) < 0.97 ? 'true' : 'false'}>
      <style>{CARD_CSS}</style>
      <div className="homc-globe">
        {frame ? <canvas ref={canvasRef} className="homc-canvas" onClick={onCanvasClick} aria-label={`Map of ${title}`} /> : null}
        {state.loading && !state.data ? (
          <div className="homc-globe-wait">
            <GlyphSpinner ariaLabel="Loading the map" />
          </div>
        ) : null}
      </div>
      <div className="homc-body">
        <div className="homc-head">
          <span className="homc-kicker">
            <span className="homc-live" data-ok={state.error ? 'false' : 'true'} /> OSINT Map
          </span>
          <span className="homc-muted homc-num">{updatedAt ? `updated ${age(updatedAt, now)} ago` : state.loading ? 'loading…' : ''}</span>
        </div>
        <div className="homc-title" title={title}>
          {title}
        </div>
        <div className="homc-sub">
          {state.data ? `${state.data.count} ${state.data.count === 1 ? 'item' : 'items'} on the map${area.lat !== undefined ? ` within ${Math.round(area.radiusKm)} km` : ''}, live` : '\u00a0'}
        </div>
        {Object.keys(counts).length ? (
          <div className="homc-chips">
            {LAYERS.filter(l => counts[l.id]).map(l => (
              <span key={l.id} className="homc-chip" title={l.label}>
                <Shape layer={l.id} size={9} />
                <span className="homc-num">{counts[l.id]}</span>
                <span className="homc-chip-label">{l.label}</span>
              </span>
            ))}
          </div>
        ) : null}
        {state.error && !state.data ? <div className="homc-error">{state.error}</div> : null}
        {top.length ? (
          <ol className="homc-list">
            {top.map(item => (
              <li key={item.id}>
                <button type="button" className="homc-row" data-picked={item.id === picked} onClick={() => setPicked(p => (p === item.id ? null : item.id))}>
                  <Shape layer={item.layer} size={10} />
                  <span className="homc-row-title">{item.title}</span>
                  <span className="homc-row-meta homc-num">
                    {area.lat !== undefined && item.lat !== null ? `${Math.round(kmBetween({ lat: area.lat, lon: area.lon }, item))} km` : item.country ?? ''}
                    {item.time ? ` · ${age(item.time, now)} ago` : ''}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        ) : state.data ? (
          <div className="homc-quiet">Nothing on the map here right now.</div>
        ) : null}
        <div className="homc-actions">
          <Button size="sm" variant="secondary" onClick={openMap}>
            <Codicon name="compass" /> Open in OSINT Map
          </Button>
          <Button size="sm" variant="ghost" onClick={watch}>
            <Codicon name="eye" /> Watch this area
          </Button>
        </div>
      </div>
    </div>
  )
}

function friendly(error) {
  const text = String(error?.message ?? error ?? '')
  if (/404|not found/i.test(text)) return 'The OSINT Map backend is still starting. The card will retry.'
  return text.slice(0, 160) || 'The map data is unavailable.'
}

function useThemeSafe() {
  try {
    return useTheme() ?? {}
  } catch {
    return {}
  }
}
