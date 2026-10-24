// Plugin-level state that outlives a page mount: layer data and its polling, satellite propagation,
// aircraft trails, selection, notable-event alerts and user preferences. The page attaches while
// mounted; polling stops a little after the last page detaches.
import { LAYERS, toItem } from './data/layers.js'
import { bearing, buildSatellites, positionAt } from './data/satellites.js'

const DETACH_GRACE_MS = 30_000
const TICK_MS = 1000
const REGISTRY_MS = 30_000
const TRAIL_POINTS = 24
const TRAIL_AIRCRAFT = 800
const DEFAULT_REFRESH_S = { quakes: 60, disasters: 600, natural: 600, weather: 300, incidents: 900, aircraft: 15, satellites: 21600, cameras: 3600, chokepoints: 86400 }

const DEFAULTS = {
  layers: Object.fromEntries(LAYERS.map(l => [l.id, l.id !== 'weather'])),
  dither: true,
  autoRotate: true,
  alerts: true
}

/**
 * @param {object} deps
 * @param {(init: any) => {get(): any, set(v: any): void, subscribe(fn): () => void}} deps.atom
 * @param {(path: string, opts?: object) => Promise<any>} deps.rest
 * @param {(fn: () => void, ms: number) => () => void} deps.schedule
 * @param {{ get(key: string, fallback: any): any, set(key: string, value: any): void }} deps.storage
 * @param {(alert: {title: string, body: string}) => void} [deps.notify]
 */
export function createController({ atom, rest, schedule, storage, notify }) {
  const saved = storage.get('settings', {}) ?? {}
  const settings = atom({
    ...DEFAULTS,
    ...saved,
    layers: { ...DEFAULTS.layers, ...(saved.layers && typeof saved.layers === 'object' ? saved.layers : {}) }
  })
  const registry = atom({ state: 'loading', layers: [], error: null }) // backend /layers
  const version = atom(0) // bumps when any layer's items change
  const selection = atom(null) // { kind: 'item', id } | { kind: 'country', code } | null
  const alerts = atom([]) // [{ id, title, layer, time }]

  const data = new Map() // layer id -> { items, byId, updated, status, error, next, inflight }
  const items = new Map() // item id -> item (all layers)
  const trails = new Map() // aircraft id -> [{lat, lon, altKm, t}]
  const alerted = new Set()
  let sats = [] // [{ item, satrec }]
  let view = { lat: 22, lon: 15, dist: 4.5, fit: 4.5 }
  let aircraftAnchor = null // {lat, lon} of the last regional query
  let attached = 0
  let running = false
  let cancelTick = null
  let cancelDetach = null
  let lastRegistry = 0
  let satTick = 0
  let firstLoad = new Set(LAYERS.map(l => l.id))

  const bump = () => version.set(version.get() + 1)

  function update(patch) {
    const next = { ...settings.get(), ...patch }
    settings.set(next)
    storage.set('settings', next)
  }

  function slot(id) {
    let d = data.get(id)
    if (!d) {
      d = { items: [], updated: null, status: 'idle', error: null, next: 0, inflight: false }
      data.set(id, d)
    }
    return d
  }

  function refreshSeconds(id) {
    const meta = registry.get().layers.find(l => l.id === id)
    return Math.max(10, meta?.refresh_s ?? DEFAULT_REFRESH_S[id] ?? 300)
  }

  function zoomedIn() {
    return view.dist < view.fit * 0.62
  }

  async function loadRegistry() {
    lastRegistry = Date.now()
    try {
      const answer = await rest('/layers')
      if (answer?.ok && Array.isArray(answer.layers)) {
        const wasDown = registry.get().state !== 'ok'
        registry.set({ state: 'ok', layers: answer.layers, error: null })
        // The backend just came up (e.g. enabled after the page opened): retry failed layers now.
        if (wasDown) for (const d of data.values()) if (d.status === 'error') d.next = 0
      }
      else registry.set({ ...registry.get(), state: 'error', error: answer?.error ?? 'Unexpected answer from the backend.' })
    } catch (error) {
      const message = String(error?.message ?? error)
      registry.set({ ...registry.get(), state: /\b404\b|not found/i.test(message) ? 'missing' : 'error', error: message })
      lastRegistry = Date.now() - REGISTRY_MS + 5000 // while down, check again in 5 s
    }
  }

  async function loadLayer(id) {
    const d = slot(id)
    d.inflight = true
    let path = `/layer/${id}`
    if (id === 'aircraft' && zoomedIn()) {
      aircraftAnchor = { lat: view.lat, lon: view.lon }
      path += `?lat=${view.lat.toFixed(3)}&lon=${view.lon.toFixed(3)}`
    } else if (id === 'aircraft') {
      aircraftAnchor = null
    }
    try {
      const answer = await rest(path)
      if (!answer?.ok || !Array.isArray(answer.items)) throw new Error(answer?.error ?? 'Unexpected answer from the backend.')
      ingest(id, answer)
      if (registry.get().state !== 'ok') lastRegistry = 0
    } catch (error) {
      d.status = d.items.length ? 'stale' : 'error'
      d.error = String(error?.message ?? error)
      bump()
    } finally {
      d.inflight = false
      // Failures retry within 30 s whatever the layer's normal cadence.
      d.next = Date.now() + (d.status === 'error' ? Math.min(30_000, refreshSeconds(id) * 500) : refreshSeconds(id) * 1000)
    }
  }

  function ingest(id, answer) {
    const d = slot(id)
    for (const old of d.items) items.delete(old.id)
    const next = []
    for (const raw of answer.items.slice(0, 8000)) {
      const item = toItem(raw, id)
      if (item) next.push(item)
    }
    d.items = next
    d.updated = typeof answer.updated === 'number' ? answer.updated : Date.now()
    d.status = answer.status === 'stale' ? 'stale' : 'ok'
    d.error = answer.error ?? null
    for (const item of next) items.set(item.id, item)
    if (id === 'satellites') {
      sats = buildSatellites(next)
      propagateSatellites()
    }
    if (id === 'aircraft') recordTrails(next)
    raiseAlerts(id, next)
    firstLoad.delete(id)
    bump()
  }

  function recordTrails(list) {
    const now = Date.now()
    for (const item of list) {
      if (item.lat === null) continue
      let trail = trails.get(item.id)
      if (!trail) {
        if (trails.size >= TRAIL_AIRCRAFT) continue
        trail = []
        trails.set(item.id, trail)
      }
      const last = trail[trail.length - 1]
      if (!last || last.lat !== item.lat || last.lon !== item.lon) trail.push({ lat: item.lat, lon: item.lon, altKm: item.altKm ?? 0, t: now })
      if (trail.length > TRAIL_POINTS) trail.shift()
    }
    for (const [id, trail] of trails) if (now - trail[trail.length - 1].t > 10 * 60_000) trails.delete(id)
  }

  function propagateSatellites() {
    const now = new Date()
    const soon = new Date(now.getTime() + 20_000)
    for (const { item, satrec } of sats) {
      const p = positionAt(satrec, now)
      if (!p) {
        item.lat = item.lon = null
        continue
      }
      item.lat = p.lat
      item.lon = p.lon
      item.altKm = p.altKm
      item.speedKms = p.speedKms
      const q = positionAt(satrec, soon)
      item.heading = q ? bearing(p, q) : 0
    }
  }

  // Notable events: big earthquakes and red/orange GDACS alerts, once each, never on first load.
  function raiseAlerts(id, list) {
    if (id !== 'quakes' && id !== 'disasters') return
    const quiet = firstLoad.has(id)
    const fresh = []
    for (const item of list) {
      const notable = id === 'quakes' ? item.severity >= 0.8 : item.severity >= 0.65
      if (!notable || alerted.has(item.id)) continue
      alerted.add(item.id)
      if (quiet) continue
      if (item.time && Date.now() - item.time > 6 * 3600_000) continue
      fresh.push({ id: item.id, title: item.title, layer: id, time: item.time ?? Date.now() })
    }
    if (!fresh.length) return
    alerts.set([...fresh, ...alerts.get()].slice(0, 30))
    if (settings.get().alerts) for (const a of fresh.slice(0, 3)) notify?.({ title: a.title, body: id === 'quakes' ? 'New significant earthquake' : 'New GDACS alert' })
  }

  async function tick() {
    cancelTick = null
    if (!running) return
    const now = Date.now()
    if (now - lastRegistry > REGISTRY_MS) loadRegistry()
    const enabled = settings.get().layers
    const jobs = []
    for (const layer of LAYERS) {
      if (!enabled[layer.id]) continue
      const d = slot(layer.id)
      if (d.inflight) continue
      let due = now >= d.next
      // Panning far while zoomed in: refresh nearby traffic now.
      if (layer.id === 'aircraft' && zoomedIn() && aircraftAnchor) {
        const moved = Math.hypot(view.lat - aircraftAnchor.lat, view.lon - aircraftAnchor.lon)
        if (moved > 2.5 && now - (d.next - refreshSeconds('aircraft') * 1000) > 4000) due = true
      }
      if (layer.id === 'aircraft' && zoomedIn() && !aircraftAnchor && now - (d.next - refreshSeconds('aircraft') * 1000) > 4000) due = true
      if (due) jobs.push(loadLayer(layer.id))
    }
    if (sats.length && enabled.satellites && now - satTick >= 1000) {
      satTick = now
      propagateSatellites()
      bump()
    }
    await Promise.allSettled(jobs.slice(0, 4))
    if (running) cancelTick = schedule(tick, TICK_MS)
  }

  function start() {
    if (running) return
    running = true
    cancelTick = schedule(tick, 0)
  }

  function stop() {
    running = false
    cancelTick?.()
    cancelTick = null
  }

  return {
    settings,
    registry,
    version,
    selection,
    alerts,

    attach() {
      attached += 1
      cancelDetach?.()
      cancelDetach = null
      start()
      return () => {
        attached -= 1
        if (attached > 0) return
        cancelDetach = schedule(() => {
          cancelDetach = null
          if (attached === 0) stop()
        }, DETACH_GRACE_MS)
      }
    },

    layerState: id => {
      const d = data.get(id)
      return d ? { status: d.status, error: d.error, updated: d.updated, count: d.items.length } : { status: 'idle', error: null, updated: null, count: 0 }
    },
    layerItems: id => data.get(id)?.items ?? [],
    allItems: () => {
      const enabled = settings.get().layers
      const out = []
      for (const layer of LAYERS) if (enabled[layer.id]) for (const item of data.get(layer.id)?.items ?? []) out.push(item)
      return out
    },
    item: id => items.get(id),
    trail: id => trails.get(id) ?? [],
    satellite: id => sats.find(s => s.item.id === id) ?? null,
    isFirstLoad: id => firstLoad.has(id),

    setView(next) {
      view = next
    },
    toggleLayer(id) {
      const layers = { ...settings.get().layers, [id]: !settings.get().layers[id] }
      update({ layers })
      if (layers[id]) slot(id).next = 0
      bump()
    },
    setDither: value => update({ dither: value }),
    setAutoRotate: value => update({ autoRotate: value }),
    setAlerts: value => update({ alerts: value }),
    refresh(id) {
      if (id) slot(id).next = 0
      else for (const d of data.values()) d.next = 0
      lastRegistry = 0
    },
    clearAlerts: () => alerts.set([]),
    rest,

    dispose() {
      stop()
      cancelDetach?.()
    }
  }
}
