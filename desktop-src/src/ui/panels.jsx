// Rail cards, detail panels and the recon drawer. Layer data comes from the controller; panel data
// (headlines, space weather, markets, country facts, recon) is fetched here from the backend.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Codicon, GlyphSpinner, Switch, Tip } from '@hermes/plugin-sdk'
import { LAYER_INDEX, LAYERS, layerOf } from '../data/layers.js'
import { periodMinutes } from '../data/satellites.js'
import { countryAt, countryByCode, countryName } from '../geo/world.js'
import { age, fmt, fmtCompact, layerVar, Shape, useNow } from './kit.jsx'

/** Poll a backend path while mounted. */
export function useRemote(rest, path, intervalMs, enabled = true) {
  const [state, setState] = useState({ loading: !!path && enabled, data: null, error: null })
  const alive = useRef(true)
  const load = useCallback(async () => {
    if (!path) return
    try {
      const answer = await rest(path)
      if (!alive.current) return
      if (answer?.ok) setState({ loading: false, data: answer, error: null })
      else setState(s => ({ loading: false, data: s.data, error: answer?.error ?? 'Unavailable' }))
    } catch (error) {
      if (alive.current) setState(s => ({ loading: false, data: s.data, error: String(error?.message ?? error) }))
    }
  }, [rest, path])
  useEffect(() => {
    alive.current = true
    if (!path || !enabled) return undefined
    setState(s => ({ ...s, loading: !s.data }))
    load()
    const id = intervalMs ? setInterval(load, intervalMs) : null
    return () => {
      alive.current = false
      if (id) clearInterval(id)
    }
  }, [path, enabled, intervalMs, load])
  return { ...state, reload: load }
}

const STATUS_TONE = { ok: 'live', stale: 'warn', error: 'bad', idle: 'idle', disabled: 'idle', loading: 'idle' }

// --- Layers ------------------------------------------------------------------------------------

export function LayersCard({ controller, settings, registry }) {
  const now = useNow(5000)
  const meta = id => registry.layers.find(l => l.id === id)
  const live = LAYERS.filter(l => settings.layers[l.id] && controller.layerState(l.id).status === 'ok').length
  const on = LAYERS.filter(l => settings.layers[l.id]).length
  return (
    <section className="hom-panel hom-card hom-rise" aria-label="Layers">
      <div className="hom-card-head">
        <span className="hom-label">Layers</span>
        <span className="hom-muted hom-num" style={{ fontSize: 10.5 }}>
          {live}/{on} live
        </span>
      </div>
      <div className="hom-chips" role="group" aria-label="Map layers">
        {LAYERS.map((layer, i) => {
          const st = controller.layerState(layer.id)
          const m = meta(layer.id)
          const enabled = !!settings.layers[layer.id]
          const disabled = m?.status === 'disabled'
          const status = !enabled ? 'idle' : disabled ? 'disabled' : st.status
          const tip = [
            layer.hint,
            m?.source ? `Source: ${m.attribution ?? m.source}` : null,
            disabled && m?.needs_key ? `Needs ${m.needs_key}` : null,
            st.error && enabled ? `Last error: ${st.error}` : null
          ]
            .filter(Boolean)
            .join('. ')
          return (
            <Tip key={layer.id} label={tip}>
              <button type="button" className="hom-chip hom-layer" aria-pressed={enabled} onClick={() => controller.toggleLayer(layer.id)}>
                <Shape layer={layer.id} size={12} />
                <span className="hom-chip-name">{layer.label}</span>
                <span className="hom-chip-count hom-num">{enabled && st.count ? fmtCompact(st.count) : ''}</span>
                <span className="hom-layer-state">
                  <span className={`hom-dot hom-tone-${STATUS_TONE[status] ?? 'idle'}`} />
                  <span className="hom-num">{enabled && st.updated ? age(st.updated, now) : ''}</span>
                </span>
              </button>
            </Tip>
          )
        })}
      </div>
    </section>
  )
}

// --- Space weather -----------------------------------------------------------------------------

const KP_WORDS = ['quiet', 'quiet', 'quiet', 'unsettled', 'active', 'minor storm', 'moderate storm', 'strong storm', 'severe storm', 'extreme storm']

export function SpaceWeatherCard({ rest }) {
  const { data, error, loading } = useRemote(rest, '/space-weather', 5 * 60_000)
  const kp = data?.kp ?? []
  const now = data?.kp_now ?? kp[kp.length - 1]?.kp ?? null
  const scales = data?.scales ?? {}
  const max = 9
  return (
    <section className="hom-panel hom-card hom-rise" style={{ animationDelay: '80ms' }} aria-label="Space weather">
      <div className="hom-card-head">
        <span className="hom-label">Space weather</span>
        <Tip label="NOAA SWPC planetary K-index (0–9, geomagnetic activity, 3-hour steps) and the NOAA scales for radio blackouts (R), solar radiation storms (S) and geomagnetic storms (G).">
          <span className="hom-muted" tabIndex={0} aria-label="About space weather">
            <Codicon name="info" size={12} />
          </span>
        </Tip>
      </div>
      {loading ? <div className="hom-skeleton" style={{ height: 70 }} /> : null}
      {error && !data ? <div className="hom-empty-note">Space weather unavailable: {error}</div> : null}
      {data ? (
        <>
          <div className="hom-big-row">
            <span>
              <span className="hom-big hom-num">{now !== null ? now.toFixed(1) : '—'}</span>
              <span className="hom-soft" style={{ marginLeft: 8 }}>
                Kp · {now !== null ? KP_WORDS[Math.min(9, Math.floor(now))] : 'no data'}
              </span>
            </span>
            <span className="hom-scales">
              {['R', 'S', 'G'].map(k => (
                <span key={k} className="hom-scale" data-level={Math.min(5, scales[k] ?? 0)}>
                  {k}
                  {scales[k] ?? 0}
                </span>
              ))}
            </span>
          </div>
          <svg className="hom-chart" viewBox="0 0 100 30" preserveAspectRatio="none" height={30} role="img" aria-label="Kp index, last 7 days">
            {kp.map((p, i) => {
              const w = 100 / kp.length
              const h = Math.max(0.8, (p.kp / max) * 29)
              const tone = p.kp >= 7 ? 'var(--ui-red)' : p.kp >= 5 ? 'var(--ui-orange)' : p.kp >= 4 ? 'var(--ui-yellow)' : 'var(--ui-text-tertiary)'
              return <rect key={i} x={i * w + w * 0.12} width={w * 0.76} y={30 - h} height={h} fill={tone} opacity={i === kp.length - 1 ? 1 : 0.75} />
            })}
            <line x1="0" x2="100" y1={30 - (5 / max) * 29} y2={30 - (5 / max) * 29} stroke="var(--ui-orange)" strokeWidth="0.4" strokeDasharray="1.5 1.5" vectorEffect="non-scaling-stroke" />
          </svg>
          <div className="hom-kv" style={{ justifyContent: 'space-between', fontSize: 9.5 }}>
            <span className="hom-mono">−7 d</span>
            <span className="hom-mono">storm level ┄ Kp 5</span>
            <span className="hom-mono">now</span>
          </div>
          {data.alerts?.[0] ? (
            <div className="hom-caption" title={data.alerts[0].headline}>
              Latest: {data.alerts[0].headline}
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  )
}

// --- Markets -----------------------------------------------------------------------------------

export function MarketsCard({ rest }) {
  const { data, error, loading } = useRemote(rest, '/markets', 5 * 60_000)
  return (
    <section className="hom-panel hom-card hom-rise" style={{ animationDelay: '140ms' }} aria-label="Markets">
      <div className="hom-card-head">
        <span className="hom-label">Markets</span>
      </div>
      {loading ? <div className="hom-skeleton" style={{ height: 60 }} /> : null}
      {error && !data ? <div className="hom-empty-note">Markets unavailable: {error}</div> : null}
      {data?.groups?.map(group => (
        <div key={group.id} className="hom-rank">
          <span className="hom-label" style={{ fontSize: 9 }}>
            {group.label}
          </span>
          {group.items.map(row => {
            const up = (row.change_pct ?? 0) >= 0
            return (
              <div key={row.symbol} className="hom-row" style={{ cursor: 'default', gridTemplateColumns: 'auto 1fr auto auto' }}>
                <span className="hom-code">{row.symbol}</span>
                <span className="hom-row-name hom-soft">{row.label}</span>
                <span className="hom-num">{row.price != null ? fmtPrice(row.price) : '—'}</span>
                <span className="hom-num hom-change" data-up={up}>
                  {row.change_pct != null ? `${up ? '▲' : '▼'} ${Math.abs(row.change_pct).toFixed(2)}%` : ''}
                </span>
              </div>
            )
          })}
        </div>
      ))}
      {data?.notes?.map(note => (
        <div key={note} className="hom-caption">
          {note}
        </div>
      ))}
    </section>
  )
}

function fmtPrice(n) {
  return n >= 1000 ? fmt(n) : n >= 1 ? n.toFixed(2) : n.toPrecision(3)
}

// --- Feed --------------------------------------------------------------------------------------

const FEED_LAYERS = ['quakes', 'disasters', 'natural', 'incidents', 'weather']

export function FeedCard({ controller, settings, rest, onItem, openExternal, alerts }) {
  const [tab, setTab] = useState('events')
  const [only, setOnly] = useState(null) // one feed layer, or all
  const now = useNow(10_000)
  const news = useRemote(rest, tab === 'headlines' ? '/news?limit=60' : null, 10 * 60_000)
  const events = FEED_LAYERS.filter(id => settings.layers[id] && (!only || only === id))
    .flatMap(id => controller.layerItems(id))
    .filter(item => item.time)
    .sort((a, b) => b.time - a.time)
    .slice(0, 80)
  return (
    <section className="hom-panel hom-feed hom-rise" style={{ animationDelay: '60ms' }} aria-label="Feed">
      <div className="hom-feed-head" style={{ paddingBottom: 6 }}>
        <div className="hom-tabs" role="tablist" aria-label="Feed" style={{ flex: 1 }}>
          {[
            ['events', 'Events'],
            ['headlines', 'Headlines'],
            ['alerts', `Alerts${alerts.length ? ` · ${alerts.length}` : ''}`]
          ].map(([id, label]) => (
            <button key={id} type="button" role="tab" className="hom-tab" aria-selected={tab === id} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {tab === 'events' ? (
        <div className="hom-feed-filter" role="group" aria-label="Show events from">
          <button type="button" className="hom-tag hom-tag--button" aria-pressed={!only} onClick={() => setOnly(null)}>
            All
          </button>
          {FEED_LAYERS.filter(id => settings.layers[id]).map(id => (
            <Tip key={id} label={layerOf(id).label}>
              <button type="button" className="hom-tag hom-tag--button" aria-pressed={only === id} onClick={() => setOnly(only === id ? null : id)} aria-label={layerOf(id).label}>
                <Shape layer={id} size={10} /> {fmtCompact(controller.layerItems(id).length)}
              </button>
            </Tip>
          ))}
        </div>
      ) : null}
      <div className="hom-feed-list" role="list">
        {tab === 'events' ? (
          events.length ? (
            events.map(item => (
              <button key={item.id} type="button" role="listitem" className="hom-event" style={{ '--hom-row-color': layerVar(LAYER_INDEX[item.layer]) }} onClick={() => onItem(item)}>
                <span className="hom-event-glyph">
                  <Shape layer={item.layer} size={11} />
                </span>
                <span style={{ minWidth: 0 }}>
                  <span className="hom-event-name">{item.title}</span>
                  <span className="hom-event-route">
                    {item.country ? <span className="hom-code">{item.country}</span> : null}
                    <span className="hom-ellipsis">{item.subtitle ?? layerOf(item.layer).label}</span>
                  </span>
                </span>
                <span className="hom-event-side">
                  <span className="hom-event-age hom-num">{age(item.time, now)}</span>
                  {item.count > 1 ? <span className="hom-badge hom-num">×{fmtCompact(item.count)}</span> : null}
                </span>
              </button>
            ))
          ) : (
            <div className="hom-empty-note" style={{ padding: '6px 8px' }}>
              Waiting for events from the enabled layers…
            </div>
          )
        ) : null}
        {tab === 'headlines' ? (
          <>
            {news.loading ? <div className="hom-skeleton" style={{ margin: 8 }} /> : null}
            {news.error && !news.data ? <div className="hom-empty-note" style={{ padding: '6px 8px' }}>Headlines unavailable: {news.error}</div> : null}
            {news.data?.items?.map(h => (
              <button key={h.id} type="button" role="listitem" className="hom-event" onClick={() => openExternal(h.url)} title="Open the article">
                <span className="hom-event-glyph">
                  <Codicon name="link-external" size={11} />
                </span>
                <span style={{ minWidth: 0 }}>
                  <span className="hom-event-name">{h.title}</span>
                  <span className="hom-event-route">
                    <span className="hom-ellipsis">{h.source}</span>
                  </span>
                </span>
                <span className="hom-event-side">
                  <span className="hom-event-age hom-num">{h.time ? age(h.time, now) : ''}</span>
                </span>
              </button>
            ))}
          </>
        ) : null}
        {tab === 'alerts' ? (
          alerts.length ? (
            alerts.map(a => (
              <button key={a.id} type="button" role="listitem" className="hom-event" onClick={() => onItem(controller.item(a.id) ?? null)}>
                <span className="hom-event-glyph">
                  <Shape layer={a.layer} size={11} />
                </span>
                <span className="hom-event-name">{a.title}</span>
                <span className="hom-event-side">
                  <span className="hom-event-age hom-num">{age(a.time, now)}</span>
                </span>
              </button>
            ))
          ) : (
            <div className="hom-empty-note" style={{ padding: '6px 8px' }}>
              No alerts yet. Significant earthquakes and orange or red GDACS alerts that arrive while the map is open show up here.
            </div>
          )
        ) : null}
      </div>
    </section>
  )
}

// --- Item detail -------------------------------------------------------------------------------

export function ItemPanel({ item, controller, onClose, onCountry, onAsk, openExternal }) {
  const now = useNow(1000)
  const layer = layerOf(item.layer)
  const country = item.country ?? (item.lat !== null ? countryAt(item.lat, item.lon)?.code : null)
  const sat = item.layer === 'satellites' ? controller.satellite(item.id) : null
  const live = []
  if (item.layer === 'satellites' && item.lat !== null) {
    live.push(['Altitude', `${fmt(item.altKm)} km`], ['Speed', item.speedKms ? `${item.speedKms.toFixed(2)} km/s` : '—'])
    if (sat) live.push(['Orbit', `${periodMinutes(sat.satrec)?.toFixed(1)} min, ${((sat.satrec.inclo * 180) / Math.PI).toFixed(1)}° inclination`])
  }
  const trail = item.layer === 'aircraft' ? controller.trail(item.id) : []
  return (
    <section className="hom-panel hom-card hom-detail" aria-label="Details">
      <div className="hom-detail-head">
        <span className="hom-label" style={{ display: 'flex', alignItems: 'center', gap: 7, color: layerVar(LAYER_INDEX[item.layer]) }}>
          <Shape layer={item.layer} size={12} /> {layer.label}
          {item.kind && item.kind !== item.layer ? <span className="hom-badge">{item.kind}</span> : null}
        </span>
        <button type="button" className="hom-btn" onClick={onClose} aria-label="Close details (Esc)">
          <Codicon name="close" />
        </button>
      </div>
      <div className="hom-detail-title">{item.title}</div>
      {item.subtitle ? <div className="hom-soft">{item.subtitle}</div> : null}
      <dl className="hom-facts">
        {item.time && item.layer !== 'satellites' ? (
          <>
            <dt>Time</dt>
            <dd className="hom-num">
              {new Date(item.time).toISOString().replace('T', ' ').slice(0, 16)} UTC · {age(item.time, now)} ago
            </dd>
          </>
        ) : null}
        {item.lat !== null ? (
          <>
            <dt>Position</dt>
            <dd className="hom-num">
              {item.lat.toFixed(3)}, {item.lon.toFixed(3)}
              {country ? ` · ${countryName(country)}` : ''}
            </dd>
          </>
        ) : null}
        {live.concat(item.props).map(([k, v], i) => (
          <FactRow key={`${k}${i}`} label={k} value={v} />
        ))}
        {trail.length > 1 ? (
          <>
            <dt>Track</dt>
            <dd>{trail.length} positions seen while the map was open (drawn on the globe)</dd>
          </>
        ) : null}
      </dl>
      <div className="hom-actions">
        <button type="button" className="hom-btn hom-btn--primary" onClick={() => onAsk(item)}>
          <Codicon name="sparkle" /> Ask Hermes
        </button>
        {item.url ? (
          <button type="button" className="hom-btn hom-btn--ghost" onClick={() => openExternal(item.url)}>
            <Codicon name="link-external" /> Source
          </button>
        ) : null}
        {country && countryByCode(country) ? (
          <button type="button" className="hom-btn hom-btn--ghost" onClick={() => onCountry(country)}>
            <span className="hom-code">{country}</span> Dossier
          </button>
        ) : null}
      </div>
    </section>
  )
}

function FactRow({ label, value }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  )
}

// --- Country dossier ---------------------------------------------------------------------------

export function CountryPanel({ code, controller, rest, onClose, onItem, onAsk, openExternal }) {
  const name = countryName(code)
  const facts = useRemote(rest, `/country/${code}`, 0)
  const news = useRemote(rest, `/news?q=${encodeURIComponent(name)}&limit=8`, 10 * 60_000)
  const now = useNow(10_000)
  const inCountry = controller
    .allItems()
    .filter(item => item.layer !== 'satellites' && (item.country === code || (item.country === null && item.lat !== null && countryAt(item.lat, item.lon)?.code === code)))
  const byLayer = LAYERS.map(l => [l, inCountry.filter(i => i.layer === l.id).length]).filter(([, n]) => n > 0)
  // Events only: aircraft are always "just seen" and would crowd out everything else.
  const recent = inCountry.filter(i => i.time && FEED_LAYERS.includes(i.layer)).sort((a, b) => b.time - a.time).slice(0, 8)
  const f = facts.data
  return (
    <section className="hom-panel hom-card hom-detail" aria-label={`${name} dossier`}>
      <div className="hom-detail-head">
        <span style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}>
          <span className="hom-code" style={{ height: 20, fontSize: 11 }}>
            {code}
          </span>
          <span className="hom-detail-title" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {name}
          </span>
        </span>
        <button type="button" className="hom-btn" onClick={onClose} aria-label="Close dossier (Esc)">
          <Codicon name="close" />
        </button>
      </div>
      {facts.loading ? <div className="hom-skeleton" style={{ height: 48 }} /> : null}
      {f ? (
        <div className="hom-pair">
          <div>
            <span className="hom-muted" style={{ fontSize: 10.5 }}>
              Population
            </span>
            <span className="hom-pair-value hom-num">{f.population ? fmtCompact(f.population) : '—'}</span>
          </div>
          <div>
            <span className="hom-muted" style={{ fontSize: 10.5 }}>
              GDP per capita
            </span>
            <span className="hom-pair-value hom-num">{f.gdp_per_capita_usd ? `$${fmtCompact(f.gdp_per_capita_usd)}` : '—'}</span>
          </div>
        </div>
      ) : null}
      {f ? (
        <dl className="hom-facts">
          {f.capital ? <FactRow label="Capital" value={f.capital} /> : null}
          {f.region ? <FactRow label="Region" value={f.region} /> : null}
          {f.income ? <FactRow label="Income group" value={f.income} /> : null}
          {f.gdp_usd ? <FactRow label="GDP" value={`$${fmtCompact(f.gdp_usd)}${f.year ? ` (${f.year})` : ''}`} /> : null}
        </dl>
      ) : null}
      {facts.error && !f ? <div className="hom-caption">World Bank facts unavailable: {facts.error}</div> : null}

      <span className="hom-label">On the map now</span>
      {byLayer.length ? (
        <div className="hom-tags">
          {byLayer.map(([l, n]) => (
            <span key={l.id} className="hom-tag">
              <Shape layer={l.id} size={10} /> {n} {l.label.toLowerCase()}
            </span>
          ))}
        </div>
      ) : (
        <div className="hom-empty-note">Nothing from the enabled layers is located in {name} right now.</div>
      )}
      {recent.length ? (
        <div className="hom-rank">
          {recent.map(item => (
            <button key={item.id} type="button" className="hom-row" onClick={() => onItem(item)}>
              <Shape layer={item.layer} size={10} />
              <span className="hom-row-name">{item.title}</span>
              <span className="hom-num hom-muted" style={{ fontSize: 10.5 }}>
                {age(item.time, now)}
              </span>
            </button>
          ))}
        </div>
      ) : null}

      <span className="hom-label">Headlines</span>
      {news.loading ? <div className="hom-skeleton" style={{ height: 40 }} /> : null}
      {news.data?.items?.length ? (
        <div className="hom-rank">
          {news.data.items.map(h => (
            <button key={h.id} type="button" className="hom-row" onClick={() => openExternal(h.url)} title={h.title}>
              <Codicon name="link-external" size={10} />
              <span className="hom-row-name">{h.title}</span>
              <span className="hom-num hom-muted" style={{ fontSize: 10.5 }}>
                {h.time ? age(h.time, now) : ''}
              </span>
            </button>
          ))}
        </div>
      ) : news.data ? (
        <div className="hom-empty-note">No recent headlines mention {name}.</div>
      ) : null}

      <div className="hom-actions">
        <button type="button" className="hom-btn hom-btn--primary" onClick={() => onAsk(code)}>
          <Codicon name="sparkle" /> Ask Hermes for a dossier
        </button>
      </div>
    </section>
  )
}

// --- Recon -------------------------------------------------------------------------------------

export const RECON_KINDS = [
  { id: 'dns', label: 'DNS', placeholder: 'example.com', hint: 'A, AAAA, MX, NS, TXT, CNAME and CAA records (Google DNS-over-HTTPS)' },
  { id: 'rdap', label: 'WHOIS', placeholder: 'example.com or 8.8.8.8', hint: 'Registration data over RDAP: registrar, dates, status, network owner' },
  { id: 'tls', label: 'TLS', placeholder: 'example.com', hint: 'Certificate subject, issuer, names and expiry from a direct handshake' },
  { id: 'headers', label: 'Headers', placeholder: 'https://example.com', hint: 'Redirect chain and which security headers are present' },
  { id: 'subdomains', label: 'Subdomains', placeholder: 'example.com', hint: 'Names seen in Certificate Transparency logs (crt.sh)' },
  { id: 'ip', label: 'IP', placeholder: '1.1.1.1', hint: 'Geolocation, ASN and network owner' },
  { id: 'cve', label: 'CVE', placeholder: 'CVE-2024-3400', hint: 'Description, CVSS, weakness, and whether CISA lists it as exploited' },
  { id: 'github', label: 'GitHub', placeholder: 'username or org', hint: 'Public profile and top repositories' },
  { id: 'mac', label: 'MAC', placeholder: '00:1A:2B:3C:4D:5E', hint: 'Hardware vendor from the address prefix' }
]

export function ReconDrawer({ rest, onClose, onAsk }) {
  const [kind, setKind] = useState('dns')
  const [target, setTarget] = useState('')
  const [run, setRun] = useState(null) // { kind, target, loading, data, error }
  const [history, setHistory] = useState([])
  const inputRef = useRef(null)
  const def = RECON_KINDS.find(k => k.id === kind)

  useEffect(() => inputRef.current?.focus(), [kind])

  async function lookup(k = kind, t = target) {
    const q = t.trim()
    if (!q) return
    setRun({ kind: k, target: q, loading: true })
    try {
      const answer = await rest(`/recon/${k}?q=${encodeURIComponent(q)}`)
      if (answer?.ok) {
        setRun({ kind: k, target: answer.target ?? q, loading: false, data: answer })
        setHistory(h => [{ kind: k, target: answer.target ?? q }, ...h.filter(x => !(x.kind === k && x.target === (answer.target ?? q)))].slice(0, 8))
      } else setRun({ kind: k, target: q, loading: false, error: answer?.error ?? 'Lookup failed.' })
    } catch (error) {
      setRun({ kind: k, target: q, loading: false, error: String(error?.message ?? error) })
    }
  }

  return (
    <section className="hom-panel hom-recon hom-detail" aria-label="Recon toolkit" onKeyDown={e => e.key === 'Escape' && (e.stopPropagation(), onClose())}>
      <div className="hom-detail-head">
        <span className="hom-label">Recon · passive lookups</span>
        <button type="button" className="hom-btn" onClick={onClose} aria-label="Close recon (Esc)">
          <Codicon name="close" />
        </button>
      </div>
      <div className="hom-recon-kinds" role="tablist" aria-label="Lookup type">
        {RECON_KINDS.map(k => (
          <Tip key={k.id} label={k.hint}>
            <button type="button" role="tab" className="hom-tab" aria-selected={kind === k.id} onClick={() => setKind(k.id)}>
              {k.label}
            </button>
          </Tip>
        ))}
      </div>
      <form
        className="hom-recon-form"
        onSubmit={e => {
          e.preventDefault()
          lookup()
        }}
      >
        <input
          ref={inputRef}
          className="hom-input hom-mono"
          value={target}
          onChange={e => setTarget(e.target.value)}
          placeholder={def.placeholder}
          spellCheck={false}
          autoComplete="off"
          aria-label={`${def.label} target`}
        />
        <button type="submit" className="hom-btn hom-btn--primary" disabled={!target.trim() || run?.loading}>
          Look up
        </button>
      </form>
      <div className="hom-caption">{def.hint}.</div>
      {run?.loading ? (
        <div className="hom-listening-inline">
          <GlyphSpinner ariaLabel="Looking up" /> Looking up {run.target}…
        </div>
      ) : null}
      {run?.error ? <div className="hom-error">{run.error}</div> : null}
      {run?.data ? <ReconResult result={run.data} onAsk={onAsk} /> : null}
      {history.length ? (
        <div className="hom-tags" aria-label="Recent lookups">
          {history.map(h => (
            <button
              key={`${h.kind}:${h.target}`}
              type="button"
              className="hom-tag hom-tag--button"
              onClick={() => {
                setKind(h.kind)
                setTarget(h.target)
                lookup(h.kind, h.target)
              }}
            >
              <span className="hom-mono">{h.kind}</span> {h.target}
            </button>
          ))}
        </div>
      ) : null}
    </section>
  )
}

function ReconResult({ result, onAsk }) {
  const [raw, setRaw] = useState(false)
  return (
    <div className="hom-recon-result">
      <div className="hom-card-head">
        <span className="hom-detail-title hom-mono" style={{ fontSize: 13.5 }}>
          {result.target}
        </span>
        <span className="hom-badge">{result.kind}</span>
      </div>
      {result.summary?.length ? (
        <dl className="hom-facts">
          {result.summary.map(([k, v], i) => (
            <FactRow key={`${k}${i}`} label={k} value={v} />
          ))}
        </dl>
      ) : (
        <div className="hom-empty-note">Nothing found.</div>
      )}
      {result.sources?.length ? <div className="hom-caption">Sources: {result.sources.join(', ')}</div> : null}
      <div className="hom-actions">
        <button type="button" className="hom-btn hom-btn--primary" onClick={() => onAsk(result.kind, result.target, result.summary)}>
          <Codicon name="sparkle" /> Ask Hermes to investigate
        </button>
        <button type="button" className="hom-btn hom-btn--ghost" onClick={() => setRaw(v => !v)} aria-expanded={raw}>
          <Codicon name="json" /> {raw ? 'Hide' : 'Raw'} data
        </button>
      </div>
      {raw ? <pre className="hom-raw">{JSON.stringify(result.result, null, 2)}</pre> : null}
    </div>
  )
}
