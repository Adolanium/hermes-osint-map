// The Cyber attacks layer: Check Point's live attack feed, relayed by the backend (/cyber/events).
// The feed is unofficial, so `toEvent` re-checks every payload the relay already validated, the
// poller backs off on failures, and playback paces each batch onto the globe as arcs.
import { countryByCode } from '../geo/world.js'

/** Attack categories. Each has a colour (a theme variable), a shape and an arc pattern, so the
 *  categories never depend on colour alone. */
export const CATEGORIES = [
  {
    id: 'exploit',
    label: 'Exploit',
    shape: 'diamond',
    pattern: 'solid',
    cssVar: '--ui-orange',
    fallbackVar: '--ui-red',
    hint: 'Attempts to exploit a software vulnerability'
  },
  {
    id: 'malware',
    label: 'Malware',
    shape: 'circle',
    pattern: 'dashed',
    cssVar: '--ui-purple',
    fallbackVar: '--ui-accent',
    hint: 'Malware delivery, infection or command traffic'
  },
  {
    id: 'phishing',
    label: 'Phishing',
    shape: 'triangle',
    pattern: 'dotted',
    cssVar: '--ui-cyan',
    fallbackVar: '--ui-blue',
    hint: 'Phishing sites, messages and lures'
  },
  {
    id: 'other',
    label: 'Other',
    shape: 'square',
    pattern: 'solid',
    cssVar: '--ui-text-secondary',
    fallbackVar: '--ui-text-primary',
    hint: 'Events the feed labelled with a category this page does not know'
  }
]

export const CATEGORY_INDEX = Object.fromEntries(CATEGORIES.map((c, i) => [c.id, i]))
export const categoryOf = type => CATEGORIES[CATEGORY_INDEX[type] ?? 3]

const COUNTRY = /^[A-Z]{2}$/

function finite(value, low, high) {
  return typeof value === 'number' && Number.isFinite(value) && value >= low && value <= high ? value : null
}

function text(value, limit) {
  if (typeof value !== 'string') return null
  const cleaned = value.replace(/\s+/g, ' ').trim()
  return cleaned ? cleaned.slice(0, limit) : null
}

function place(raw) {
  const p = raw && typeof raw === 'object' ? raw : {}
  const country = typeof p.country === 'string' && COUNTRY.test(p.country) ? p.country : null
  let lat = finite(p.lat, -90, 90)
  let lon = finite(p.lon, -180, 180)
  let located = 'exact'
  if (lat === null || lon === null) {
    // No usable coordinates: fall back to the country's label point.
    const c = countryByCode(country)
    lat = c ? c.lat : null
    lon = c ? c.lon : null
    located = c ? 'country' : 'none'
  }
  return { country, lat, lon, located, region: text(p.region, 80), city: text(p.city, 80) }
}

/**
 * Backend/demo payload -> page event, or null when it cannot be shown.
 * @param {object} raw
 * @param {{ idPrefix: string, demo: boolean }} origin
 */
export function toEvent(raw, origin) {
  if (!raw || typeof raw !== 'object') return null
  const seq = Number.isSafeInteger(raw.seq) && raw.seq > 0 ? raw.seq : null
  if (seq === null) return null
  const type = CATEGORY_INDEX[raw.type] !== undefined ? raw.type : 'other'
  const count = Number.isSafeInteger(raw.count) && raw.count > 0 ? Math.min(raw.count, 1_000_000) : 1
  const src = place(raw.src)
  const dst = place(raw.dst)
  if (!src.country && src.located === 'none') return null
  if (!dst.country && dst.located === 'none') return null
  const received = finite(raw.received, 0, 8.64e15) ?? Date.now()
  const repeatOf = Number.isSafeInteger(raw.repeat_of) && raw.repeat_of > 0 && raw.repeat_of < seq ? raw.repeat_of : null
  return {
    id: `${origin.idPrefix}${seq}`,
    seq,
    type,
    name: text(raw.name, 160) ?? 'Unnamed attack',
    count,
    src,
    dst,
    received,
    repeatOf: repeatOf === null ? null : `${origin.idPrefix}${repeatOf}`,
    repeats: 0,
    demo: origin.demo
  }
}

export const isDomestic = event => !!event.src.country && event.src.country === event.dst.country

/** Whether an event's arc can be drawn (both ends placed). */
export const isPlaceable = event => event.src.lat !== null && event.dst.lat !== null

// --- Playback --------------------------------------------------------------------------------
// Polls deliver events in batches; launching a batch at once looks like a stutter, so each batch
// is spread across the poll interval. Under a burst the visual rate is capped and the overflow is
// skipped: only the arcs are sampled.
export function createPlayback({ spreadMs = 1000, maxPerSecond = 24, maxLagMs = 2500 } = {}) {
  const minGap = 1000 / maxPerSecond
  let queue = []
  let lastAt = -Infinity
  let skipped = 0

  return {
    /** Schedule a batch that arrived at `now`. Returns how many were skipped. */
    push(events, now) {
      if (events.length === 0) return 0
      const gap = Math.max(minGap, spreadMs / events.length)
      let dropped = 0
      for (const event of events) {
        const at = Math.max(now, lastAt + gap)
        if (at - now > maxLagMs) {
          dropped += 1
          continue
        }
        queue.push({ at, event })
        lastAt = at
      }
      skipped += dropped
      return dropped
    },
    /** Events whose launch time has come, oldest first. */
    due(now) {
      if (queue.length === 0 || queue[0].at > now) return []
      let n = 0
      while (n < queue.length && queue[n].at <= now) n++
      const out = queue.slice(0, n).map(item => item.event)
      queue = queue.slice(n)
      return out
    },
    /** Shift every pending launch (used when the visual clock was paused). */
    delay(ms) {
      for (const item of queue) item.at += ms
      lastAt += ms
    },
    clear() {
      queue = []
      lastAt = -Infinity
    },
    size: () => queue.length,
    skipped: () => skipped
  }
}

// --- Polling ---------------------------------------------------------------------------------

const BACKLOG_VISUALS = 20

/** Classify a ctx.rest failure. */
export function describeRestError(error) {
  const message = String(error?.message ?? error ?? 'Unknown error')
  if (/\b404\b|not found/i.test(message)) {
    return { state: 'backend-missing', error: message }
  }
  return { state: 'backend-error', error: message }
}

/**
 * Polls the backend relay. `rest(path)` is ctx.rest; `schedule(fn, ms)` returns a canceller.
 * onBatch(events, { first, backlog, gap }) gets validated events; onStatus(status) gets the merged status.
 */
export function createCyberSource({ rest, schedule, onBatch, onStatus, intervalMs = 1000 }) {
  let cursor = 0
  let streamId = null
  let failures = 0
  let cancel = null
  let stopped = false
  let first = true

  async function tick() {
    cancel = null
    let answer
    try {
      answer = await rest(`/cyber/events?after=${cursor}&limit=500`)
    } catch (error) {
      if (stopped) return
      failures += 1
      onStatus({ ...describeRestError(error), source: 'live', retry_at: Date.now() + backoff(failures) })
      cancel = schedule(tick, backoff(failures))
      return
    }
    if (stopped) return
    if (!answer || typeof answer !== 'object' || answer.ok === false || !Array.isArray(answer.events)) {
      failures += 1
      const error = answer && typeof answer.error === 'string' ? answer.error : 'The backend sent an unexpected answer.'
      onStatus({ state: 'backend-error', error, source: 'live', retry_at: Date.now() + backoff(failures) })
      cancel = schedule(tick, backoff(failures))
      return
    }
    failures = 0
    if (answer.stream_id !== streamId) {
      // The gateway restarted (new hub): sequence numbers start over.
      if (streamId !== null) cursor = 0
      streamId = typeof answer.stream_id === 'string' ? answer.stream_id : null
    }
    const prefix = `live:${streamId ?? 'x'}:`
    const events = []
    for (const raw of answer.events) {
      const event = toEvent(raw, { idPrefix: prefix, demo: false })
      if (event) events.push(event)
      if (Number.isSafeInteger(raw?.seq) && raw.seq > cursor) cursor = raw.seq
    }
    if (Number.isSafeInteger(answer.seq) && answer.seq > cursor) cursor = answer.seq
    onBatch(events, { first, backlog: first && events.length > BACKLOG_VISUALS, gap: !first && answer.gap === true })
    first = false
    onStatus({
      source: 'live',
      state: answer.state,
      error: answer.error ?? null,
      retry_at: answer.retry_at ?? null,
      connected_at: answer.connected_at ?? null,
      last_event_at: answer.last_event_at ?? null,
      counter: answer.counter ?? null,
      stats: answer.stats ?? null
    })
    cancel = schedule(tick, intervalMs)
  }

  return {
    start() {
      stopped = false
      first = true
      if (!cancel) cancel = schedule(tick, 0)
    },
    stop() {
      stopped = true
      cancel?.()
      cancel = null
      rest('/cyber/stop', { method: 'POST' }).catch(() => {})
    },
    /** Skip any backoff and poll now. */
    retry() {
      if (stopped) return
      cancel?.()
      cancel = schedule(tick, 0)
    }
  }
}

function backoff(failures) {
  return Math.min(15_000, 1000 * 2 ** Math.min(4, failures - 1))
}
