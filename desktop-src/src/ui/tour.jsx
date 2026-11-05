// The situation tour: the globe flies from one live hotspot to the next on its own, with a caption
// at each stop. Every stop comes from what the layers hold right now; none is scripted.
import { useEffect } from 'react'
import { Codicon } from '@hermes/plugin-sdk'
import { quakeMagnitude } from '../data/layers.js'
import { countryName } from '../geo/world.js'
import { age, fmtCompact } from './kit.jsx'

export const TOUR_DWELL_MS = 6500
const DAY = 24 * 3600_000

const strongest = (list, score) => list.reduce((best, item) => (best === null || score(item) > score(best) ? item : best), null)

/** Up to six stops from live data, in a fixed order so the tour reads like a briefing. */
export function buildTour({ controller, cyber, now = Date.now() }) {
  const stops = []
  const items = id => controller.layerItems(id).filter(i => i.lat !== null)
  const recent = (i, ms) => !i.time || now - i.time <= ms

  const quake = strongest(items('quakes').filter(i => recent(i, DAY) && quakeMagnitude(i) !== null), i => quakeMagnitude(i))
  if (quake) stops.push({ kind: 'item', id: quake.id, kicker: 'Strongest earthquake · last 24 hours', title: quake.title, detail: [quake.subtitle, quake.time ? `${age(quake.time, now)} ago` : null] })

  const alert = strongest(items('disasters'), i => i.severity)
  if (alert) stops.push({ kind: 'item', id: alert.id, kicker: 'Most severe disaster alert · GDACS', title: alert.title, detail: [alert.subtitle] })

  const natural = strongest(items('natural').filter(i => recent(i, 7 * DAY)), i => i.severity + (i.time ?? 0) / 1e14)
  if (natural) stops.push({ kind: 'item', id: natural.id, kicker: 'Natural event · NASA EONET', title: natural.title, detail: [natural.subtitle] })

  if (cyber?.topTarget) {
    const { country, count } = cyber.topTarget
    stops.push({ kind: 'country', code: country, kicker: 'Most attacked right now · cyber attacks', title: countryName(country), detail: [`${fmtCompact(count)} attacks in the last five minutes`, 'Check Point ThreatCloud'] })
  }

  const cameras = items('cameras')
  const camera = cameras.find(c => c.id.startsWith('cameras:hk:')) ?? cameras.find(c => c.id.startsWith('cameras:ca:')) ?? cameras[0]
  if (camera) stops.push({ kind: 'item', id: camera.id, kicker: 'Live traffic camera', title: camera.title, detail: [camera.subtitle] })

  const iss = items('satellites').find(s => /\bISS\b/.test(s.title)) ?? items('satellites')[0]
  if (iss) stops.push({ kind: 'item', id: iss.id, kicker: 'In orbit now', title: iss.title, detail: [iss.subtitle, 'next orbit drawn'] })

  return stops.map(s => ({ ...s, detail: s.detail.filter(Boolean).join(' · ') }))
}

/** Drives the tour: performs each stop, then advances after TOUR_DWELL_MS. */
export function useTour(tour, setTour, { selectItem, selectCountry, controller, onDone }) {
  useEffect(() => {
    if (!tour) return undefined
    const stop = tour.stops[tour.index]
    if (!stop) {
      onDone()
      return undefined
    }
    if (stop.kind === 'country') selectCountry(stop.code)
    else {
      const item = controller.item(stop.id)
      if (item) selectItem(item)
    }
    const id = setTimeout(() => setTour(t => (t ? { ...t, index: t.index + 1 } : t)), TOUR_DWELL_MS)
    return () => clearTimeout(id)
  }, [tour?.index, tour?.stops]) // eslint-disable-line react-hooks/exhaustive-deps
}

export function TourCaption({ tour, onStop, onBrief }) {
  const stop = tour.stops[tour.index]
  if (!stop) return null
  return (
    <div className="hom-tour" role="status" aria-live="polite">
      <div key={tour.index} className="hom-tour-card hom-panel">
        <span className="hom-tour-kicker hom-mono">
          {String(tour.index + 1).padStart(2, '0')} / {String(tour.stops.length).padStart(2, '0')} · {stop.kicker}
        </span>
        <span className="hom-tour-title">{stop.title}</span>
        {stop.detail ? <span className="hom-tour-detail">{stop.detail}</span> : null}
        <span className="hom-tour-bar" aria-hidden="true">
          <i style={{ animationDuration: `${TOUR_DWELL_MS}ms` }} />
        </span>
        <span className="hom-tour-actions">
          <button type="button" className="hom-btn hom-btn--ghost" onClick={onBrief}>
            <Codicon name="sparkle" /> Brief me on these
          </button>
          <button type="button" className="hom-btn hom-btn--ghost" onClick={onStop}>
            <Codicon name="debug-stop" /> Stop tour
          </button>
        </span>
      </div>
    </div>
  )
}
