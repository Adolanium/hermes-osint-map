// The Cyber attacks layer on the page: a hook that polls the relay and paces arcs onto the globe,
// and the rail card that turns it on and shows what is flying. Off by default: the feed behind it
// is Check Point's public threat map, which is not a documented API.
import { useEffect, useState } from 'react'
import { Tip } from '@hermes/plugin-sdk'
import { CATEGORIES, createCyberSource, createPlayback, isPlaceable } from '../data/cyber.js'
import { countryName } from '../geo/world.js'
import { age, fmtCompact, useNow } from './kit.jsx'

const IDLE = { status: null, recent: [], perMinute: 0, byType: {} }
const RECENT = 4

export const cyberColor = c => `var(${c.cssVar}, var(${c.fallbackVar}))`

export function CyberShape({ type, size = 11 }) {
  const c = CATEGORIES.find(k => k.id === type) ?? CATEGORIES[3]
  const s = size
  const h = s / 2
  const shape =
    c.shape === 'diamond' ? (
      <path d={`M${h} 0.5 L${s - 0.5} ${h} L${h} ${s - 0.5} L0.5 ${h}Z`} />
    ) : c.shape === 'circle' ? (
      <circle cx={h} cy={h} r={h - 1} />
    ) : c.shape === 'triangle' ? (
      <path d={`M${h} 0.8 L${s - 0.6} ${s - 1.2} L0.6 ${s - 1.2}Z`} />
    ) : (
      <rect x="1.2" y="1.2" width={s - 2.4} height={s - 2.4} rx="1" />
    )
  return (
    <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} aria-hidden="true" style={{ fill: cyberColor(c), flex: 'none' }}>
      {shape}
    </svg>
  )
}

/**
 * Poll /cyber/events while `enabled`, launch each attack on the globe as an arc, and report the
 * status, the attack rate and the latest attacks for the card.
 */
export function useCyberLayer({ rest, globeRef, enabled, mask }) {
  const [state, setState] = useState(IDLE)

  useEffect(() => {
    if (!enabled) {
      globeRef.current?.clearAttacks()
      setState(IDLE)
      return undefined
    }
    const playback = createPlayback()
    const arrivals = []
    const byType = {}
    let recent = []
    let status = { state: 'connecting' }
    const schedule = (fn, ms) => {
      const id = setTimeout(fn, ms)
      return () => clearTimeout(id)
    }
    const source = createCyberSource({
      rest,
      schedule,
      onBatch(events, { first, backlog }) {
        // The first poll returns what the relay already buffered: list it, but count only live arrivals.
        const at = Date.now()
        if (!first) {
          for (const event of events) {
            arrivals.push(at)
            byType[event.type] = (byType[event.type] ?? 0) + 1
          }
        }
        if (events.length) recent = [...events.slice(-RECENT).reverse(), ...recent].slice(0, RECENT)
        // Draw only the backlog's tail, so turning the layer on isn't a flood of arcs.
        playback.push((backlog ? events.slice(-20) : events).filter(isPlaceable), performance.now())
      },
      onStatus(next) {
        status = next
      }
    })
    source.start()
    const launcher = setInterval(() => {
      const globe = globeRef.current
      for (const event of playback.due(performance.now())) {
        if (event.repeatOf) globe?.pulseAttack(event)
        else globe?.launchAttack(event)
      }
    }, 40)
    const publish = () => {
      const cutoff = Date.now() - 60_000
      while (arrivals.length && arrivals[0] < cutoff) arrivals.shift()
      setState({ status, recent, perMinute: arrivals.length, byType: { ...byType } })
    }
    const ticker = setInterval(publish, 1000)
    publish()
    return () => {
      source.stop()
      clearInterval(launcher)
      clearInterval(ticker)
      playback.clear()
      globeRef.current?.clearAttacks()
    }
  }, [enabled, rest, globeRef])

  useEffect(() => {
    globeRef.current?.setAttackMask(CATEGORIES.map(c => mask?.[c.id] !== false))
  }, [mask, globeRef])

  return state
}

const TONE = { live: 'live', connecting: 'warn', reconnecting: 'warn', idle: 'idle', error: 'bad', 'backend-error': 'bad', 'backend-missing': 'bad' }

const placeName = p => (p?.country ? countryName(p.country) || p.country : 'Unknown')

/** Rail card: the layer's switch, attack types, rate and the latest attacks. */
export function CyberCard({ enabled, mask, cyber, onToggle, onToggleType }) {
  const now = useNow(5000)
  const st = cyber.status
  const tone = !enabled ? 'idle' : TONE[st?.state] ?? 'warn'
  const today = st?.counter?.today
  const tip =
    'Live attacks detected by Check Point ThreatCloud, from the public map at threatmap.checkpoint.com. ' +
    'An unofficial feed: it can change or stop without notice. Arcs run from the attack source to its target.'
  return (
    <section className="hom-panel hom-card hom-rise" style={{ animationDelay: '40ms' }} aria-label="Cyber attacks">
      <div className="hom-chips" role="group" aria-label="Cyber attacks layer">
        <Tip label={st?.error && enabled ? `${tip} Last error: ${st.error}` : tip}>
          <button type="button" className="hom-chip hom-layer" aria-pressed={enabled} onClick={onToggle}>
            <CyberShape type="exploit" size={12} />
            <span className="hom-chip-name">Cyber attacks</span>
            <span className="hom-chip-count hom-num">{enabled && cyber.perMinute ? `${fmtCompact(cyber.perMinute)}/min` : ''}</span>
            <span className="hom-layer-state">
              <span className={`hom-dot hom-tone-${tone}`} data-pulse={enabled && st?.state === 'live'} />
              <span className="hom-num">{enabled ? (st?.state === 'live' ? 'live' : st?.state === 'connecting' ? '…' : '') : 'off'}</span>
            </span>
          </button>
        </Tip>
      </div>
      {enabled ? (
        <>
          <div className="hom-cyber-types" role="group" aria-label="Attack types">
            {CATEGORIES.slice(0, 3).map(c => (
              <Tip key={c.id} label={c.hint}>
                <button type="button" className="hom-tag hom-tag--button" aria-pressed={mask?.[c.id] !== false} onClick={() => onToggleType(c.id)}>
                  <CyberShape type={c.id} size={9} /> {c.label}
                  <span className="hom-muted hom-num">{fmtCompact(cyber.byType[c.id] ?? 0)}</span>
                </button>
              </Tip>
            ))}
          </div>
          {cyber.recent.length ? (
            <div className="hom-cyber-list" aria-label="Latest attacks">
              {cyber.recent.map(e => (
                <div key={e.id} className="hom-cyber-row">
                  <CyberShape type={e.type} size={9} />
                  <span className="hom-cyber-name" title={e.name}>
                    {e.name}
                  </span>
                  <span className="hom-muted hom-num">{age(e.received, now)}</span>
                  <span className="hom-cyber-route hom-muted">
                    {placeName(e.src)} → {placeName(e.dst)}
                    {e.count > 1 ? ` · ×${fmtCompact(e.count)}` : ''}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="hom-caption">{st?.state === 'live' ? 'Waiting for the next attack…' : st?.error ?? 'Connecting to the feed…'}</div>
          )}
          <div className="hom-caption">
            {Number.isFinite(today) ? `${fmtCompact(today)} attacks detected today · ` : ''}Check Point ThreatCloud, unofficial feed
          </div>
        </>
      ) : null}
    </section>
  )
}
