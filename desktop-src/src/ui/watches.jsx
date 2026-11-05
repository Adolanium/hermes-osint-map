// Watches on the page: the dialog that starts one, and the rail card that lists them.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Codicon, GlyphSpinner, Tip } from '@hermes/plugin-sdk'
import { layerOf } from '../data/layers.js'
import { countryName } from '../geo/world.js'
import { createWatch, DEFAULT_WATCH_LAYERS, DELIVERIES, listWatches, removeWatch, SCHEDULES, setWatchEnabled, WATCH_LAYERS } from '../watch.js'
import { age, Shape, useNow } from './kit.jsx'

const MAGNITUDES = [4.5, 5, 6]
const LEVELS = [
  { id: 'orange', label: 'Orange and red' },
  { id: 'red', label: 'Red only' }
]
const RADII = [100, 250, 500, 1000, 2000]

export function areaLabel(spec) {
  if (spec.country && spec.lat === null) return countryName(spec.country)
  if (spec.label) return spec.label
  return `${Math.round(spec.radiusKm ?? 500)} km around ${spec.lat.toFixed(1)}, ${spec.lon.toFixed(1)}`
}

/**
 * Start a watch. `draft` is `{ lat, lon, radiusKm, country, label }`: a point with a radius, or a
 * country with lat/lon null.
 */
export function WatchDialog({ draft, host, rest, onClose, onCreated }) {
  const point = draft.lat !== null && draft.lat !== undefined
  const [label, setLabel] = useState(draft.label || (draft.country ? countryName(draft.country) : ''))
  const [radiusKm, setRadiusKm] = useState(() => (point ? RADII.reduce((a, b) => (Math.abs(b - (draft.radiusKm ?? 500)) < Math.abs(a - (draft.radiusKm ?? 500)) ? b : a)) : null))
  const [layers, setLayers] = useState(DEFAULT_WATCH_LAYERS)
  const [minMagnitude, setMinMagnitude] = useState(4.5)
  const [minAlertLevel, setMinAlertLevel] = useState('orange')
  const [schedule, setSchedule] = useState('every 1h')
  const [deliver, setDeliver] = useState('local')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const nameRef = useRef(null)
  useEffect(() => nameRef.current?.focus(), [])

  const toggle = id => setLayers(ls => (ls.includes(id) ? ls.filter(l => l !== id) : [...ls, id]))
  const spec = {
    label: label.trim() || areaLabel({ ...draft, radiusKm }),
    lat: point ? draft.lat : null,
    lon: point ? draft.lon : null,
    radiusKm,
    country: point ? null : draft.country,
    layers: WATCH_LAYERS.filter(l => layers.includes(l)),
    minMagnitude,
    minAlertLevel,
    schedule,
    deliver
  }
  const start = async () => {
    if (!spec.layers.length) {
      setError('Pick at least one thing to watch for.')
      return
    }
    setBusy(true)
    setError(null)
    const result = await createWatch({ host, rest }, spec)
    setBusy(false)
    if (result.ok) onCreated(spec, result)
    else setError(result.error)
  }

  return (
    <div className="hom-center" onKeyDown={e => e.key === 'Escape' && (e.stopPropagation(), onClose())}>
      <div className="hom-panel hom-dialog hom-watch" role="dialog" aria-modal="true" aria-label="Watch this area">
        <div className="hom-watch-head">
          <h2>
            <Codicon name="eye" /> Watch this area
          </h2>
          <button type="button" className="hom-btn" onClick={onClose} aria-label="Close">
            <Codicon name="close" />
          </button>
        </div>
        <p>Hermes checks this area on a schedule and tells you only when something new shows up. Nothing is sent when it is quiet.</p>

        <label className="hom-field">
          <span className="hom-label">Name</span>
          <input ref={nameRef} className="hom-input" value={label} maxLength={60} onChange={e => setLabel(e.target.value)} placeholder={areaLabel({ ...draft, radiusKm })} />
        </label>

        <div className="hom-field">
          <span className="hom-label">Area</span>
          {point ? (
            <div className="hom-watch-line">
              <span className="hom-soft hom-num">
                {draft.lat.toFixed(2)}, {draft.lon.toFixed(2)}
              </span>
              <div className="hom-seg" role="group" aria-label="Radius">
                {RADII.map(r => (
                  <button key={r} type="button" className="hom-btn" aria-pressed={radiusKm === r} onClick={() => setRadiusKm(r)}>
                    {r >= 1000 ? `${r / 1000}k` : r} km
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <span className="hom-soft">All of {countryName(draft.country)}</span>
          )}
        </div>

        <div className="hom-field">
          <span className="hom-label">Tell me about</span>
          <div className="hom-tags">
            {WATCH_LAYERS.map(id => (
              <button key={id} type="button" className="hom-tag hom-tag--button" aria-pressed={layers.includes(id)} onClick={() => toggle(id)}>
                <Shape layer={id} size={10} /> {id === 'aircraft' ? 'Military aircraft' : layerOf(id).label}
              </button>
            ))}
          </div>
        </div>

        {layers.includes('quakes') || layers.includes('disasters') ? (
          <div className="hom-watch-grid">
            {layers.includes('quakes') ? (
              <div className="hom-field">
                <span className="hom-label">Earthquakes from</span>
                <div className="hom-seg" role="group" aria-label="Smallest magnitude">
                  {MAGNITUDES.map(m => (
                    <button key={m} type="button" className="hom-btn" aria-pressed={minMagnitude === m} onClick={() => setMinMagnitude(m)}>
                      M{m}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            {layers.includes('disasters') ? (
              <div className="hom-field">
                <span className="hom-label">Disaster alerts</span>
                <div className="hom-seg" role="group" aria-label="Lowest alert level">
                  {LEVELS.map(l => (
                    <button key={l.id} type="button" className="hom-btn" aria-pressed={minAlertLevel === l.id} onClick={() => setMinAlertLevel(l.id)}>
                      {l.label}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="hom-watch-grid">
          <div className="hom-field">
            <span className="hom-label">Check</span>
            <select className="hom-input" value={schedule} onChange={e => setSchedule(e.target.value)}>
              {SCHEDULES.map(s => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          <div className="hom-field">
            <span className="hom-label">Send alerts to</span>
            <select className="hom-input" value={deliver} onChange={e => setDeliver(e.target.value)}>
              {DELIVERIES.map(d => (
                <option key={d.id} value={d.id}>
                  {d.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <p className="hom-caption">
          A Hermes scheduled job runs each check: one short model call using your default model. The first check records what is already there, so you
          only hear about new things. Manage it here or in Scheduled jobs.
        </p>
        {error ? (
          <div className="hom-watch-error" role="alert">
            {error}
          </div>
        ) : null}
        <div className="hom-dialog-actions">
          <button type="button" className="hom-btn hom-btn--primary" disabled={busy} onClick={start}>
            {busy ? <GlyphSpinner ariaLabel="Starting" /> : <Codicon name="eye" />} Start watching
          </button>
          <button type="button" className="hom-btn hom-btn--ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

/** Rail card: every watch, its last check and latest alert. Polls while the page is open. */
export function WatchesCard({ host, rest, version, onShow, notify }) {
  const [state, setState] = useState({ loading: true, watches: [], error: null })
  const [confirm, setConfirm] = useState(null)
  const now = useNow(30_000)
  const seenAlerts = useRef(null)

  const load = useCallback(async () => {
    const result = await listWatches({ host, rest }).catch(e => ({ ok: false, error: String(e?.message ?? e), watches: [] }))
    setState({ loading: false, watches: result.watches ?? [], error: result.ok ? null : result.error })
    // Toast alerts that arrived since the last poll (not the ones already there when the page opened).
    const latest = new Map((result.watches ?? []).map(w => [w.jobId, w.alerts[0]?.at ?? 0]))
    if (seenAlerts.current) {
      for (const w of result.watches ?? []) {
        const at = latest.get(w.jobId)
        if (at && at > (seenAlerts.current.get(w.jobId) ?? 0)) notify?.(`${w.label}: ${w.alerts[0].count} new on the map`)
      }
    }
    seenAlerts.current = latest
  }, [host, rest, notify])

  useEffect(() => {
    load()
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [load, version])

  const act = async (fn, watch, ...args) => {
    const result = await fn({ host, rest }, watch, ...args)
    if (!result.ok) notify?.(`Could not update ${watch.label}: ${result.error}`)
    setConfirm(null)
    load()
  }

  if (state.loading) return null
  if (!state.watches.length && !state.error) return null
  return (
    <section className="hom-panel hom-card hom-rise" aria-label="Watches">
      <div className="hom-card-head">
        <span className="hom-label">Watches</span>
        <span className="hom-muted hom-num" style={{ fontSize: 10.5 }}>
          {state.watches.length}
        </span>
      </div>
      {state.error ? <div className="hom-caption">Scheduled jobs unavailable: {state.error}</div> : null}
      <div className="hom-watch-list">
        {state.watches.map(w => {
          const alert = w.alerts[0]
          return (
            <div key={w.jobId} className="hom-watch-item" data-enabled={w.enabled}>
              <div className="hom-watch-row">
                <span className="hom-dot" data-pulse={w.enabled} aria-hidden="true" />
                <button type="button" className="hom-link hom-watch-name" onClick={() => w.area && onShow(w.area)} disabled={!w.area} title="Show on the map">
                  {w.label}
                </button>
                <Tip label={w.enabled ? 'Pause' : 'Resume'}>
                  <button type="button" className="hom-btn" onClick={() => act(setWatchEnabled, w, !w.enabled)} aria-label={w.enabled ? 'Pause watch' : 'Resume watch'}>
                    <Codicon name={w.enabled ? 'debug-pause' : 'play'} />
                  </button>
                </Tip>
                <Tip label={confirm === w.jobId ? 'Click again to remove' : 'Remove'}>
                  <button
                    type="button"
                    className="hom-btn"
                    data-danger={confirm === w.jobId}
                    onClick={() => (confirm === w.jobId ? act(removeWatch, w) : setConfirm(w.jobId))}
                    aria-label="Remove watch"
                  >
                    <Codicon name={confirm === w.jobId ? 'check' : 'trash'} />
                  </button>
                </Tip>
              </div>
              <div className="hom-caption hom-num">
                {w.schedule ?? ''}
                {w.lastRun ? ` · checked ${age(Date.parse(w.lastRun), now)} ago` : ' · first check pending'}
                {w.error ? ` · ${w.error}` : ''}
              </div>
              {alert ? (
                <div className="hom-watch-alert">
                  <Shape layer={alert.items[0]?.layer ?? 'quakes'} size={9} />
                  <span>{alert.items[0]?.title ?? `${alert.count} new`}</span>
                  <span className="hom-muted hom-num">{age(alert.at, now)}</span>
                </div>
              ) : w.checks ? (
                <div className="hom-caption">Quiet so far · {w.matchingNow ?? 0} known in the area</div>
              ) : null}
            </div>
          )
        })}
      </div>
    </section>
  )
}
