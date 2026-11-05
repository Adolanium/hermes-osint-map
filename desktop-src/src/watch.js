// Watches from the page: a normal Hermes scheduled job per watch, plus what osint_watch remembers.
// The job is created, paused, resumed and removed through the gateway's cron.manage method, so it
// shows up in Hermes's own Scheduled jobs too and keeps running while the page is closed. The
// prompt comes from the backend (/watches/prompt), which validates the area first, so a bad area
// fails before any job exists.

export const JOB_PREFIX = 'OSINT watch · '
export const SCHEDULES = [
  { id: 'every 15m', label: 'Every 15 minutes' },
  { id: 'every 1h', label: 'Every hour' },
  { id: 'every 6h', label: 'Every 6 hours' }
]
export const DELIVERIES = [
  { id: 'local', label: 'Hermes only (Scheduled jobs)' },
  { id: 'all', label: 'All connected messaging channels' },
  { id: 'telegram', label: 'Telegram' },
  { id: 'discord', label: 'Discord' },
  { id: 'slack', label: 'Slack' },
  { id: 'whatsapp', label: 'WhatsApp' },
  { id: 'signal', label: 'Signal' },
  { id: 'email', label: 'Email' }
]
export const WATCH_LAYERS = ['quakes', 'disasters', 'natural', 'weather', 'aircraft', 'incidents']
export const DEFAULT_WATCH_LAYERS = ['quakes', 'disasters', 'natural', 'weather', 'aircraft']

/** A stable, readable id: the label's slug plus a short random suffix. */
export function watchId(label, random = Math.random) {
  const slug = String(label || 'area')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 30) || 'area'
  const suffix = Math.floor(random() * 36 ** 4).toString(36).padStart(4, '0')
  return `${/^[a-z0-9]/.test(slug) ? slug : `w-${slug}`}-${suffix}`.slice(0, 41)
}

const errorText = e => String(e?.message ?? e ?? 'unknown error').replace(/^Error:\s*/, '').slice(0, 240)

/** Create the job. Resolves `{ ok, id, job }` or `{ ok: false, error }`. */
export async function createWatch({ host, rest }, spec) {
  const id = watchId(spec.label)
  const q = new URLSearchParams({ watch_id: id, label: spec.label })
  if (spec.country) q.set('country', spec.country)
  if (spec.lat !== null && spec.lat !== undefined) {
    q.set('lat', String(spec.lat))
    q.set('lon', String(spec.lon))
    q.set('radius', String(Math.round(spec.radiusKm)))
  }
  q.set('layers', spec.layers.join(','))
  q.set('min_magnitude', String(spec.minMagnitude))
  q.set('min_alert_level', spec.minAlertLevel)
  let prompt
  let label
  try {
    const answer = await rest(`/watches/prompt?${q}`)
    if (!answer?.ok) return { ok: false, error: answer?.error ?? 'The backend refused this watch.' }
    prompt = answer.prompt
    label = answer.label
  } catch (e) {
    return { ok: false, error: errorText(e) }
  }
  try {
    const job = await host.request('cron.manage', {
      action: 'add',
      name: `${JOB_PREFIX}${label}`,
      schedule: spec.schedule,
      prompt,
      deliver: spec.deliver
    })
    if (job && job.success === false) return { ok: false, error: job.error ?? 'Hermes did not create the job.' }
    return { ok: true, id, job }
  } catch (e) {
    return { ok: false, error: errorText(e) }
  }
}

const WATCH_IN_PROMPT = /watch_id="([a-z0-9-]{3,41})"/

/** Jobs that are watches, joined with what osint_watch remembers about each. */
export async function listWatches({ host, rest }) {
  const [jobs, memory] = await Promise.all([
    host.request('cron.manage', { action: 'list', include_disabled: true }).catch(e => ({ error: errorText(e) })),
    rest('/watches').catch(() => null)
  ])
  if (jobs?.error && !Array.isArray(jobs?.jobs)) return { ok: false, error: jobs.error, watches: [] }
  return { ok: true, watches: joinWatches(jobs?.jobs ?? [], memory?.watches ?? []) }
}

/**
 * Pair cron jobs with watch records. A job is a watch when its name carries JOB_PREFIX (the page
 * made it) or its prompt preview mentions osint_watch (the agent made it from a chat). The preview
 * is cut at 100 characters, so when the watch id isn't in it the job is matched to a record whose
 * label appears in the job's name or preview.
 */
export function joinWatches(jobs, records) {
  const byId = new Map(records.map(w => [w.id, w]))
  const claimed = new Set()
  const candidates = jobs.filter(job => typeof job?.name === 'string' && (job.name.startsWith(JOB_PREFIX) || /osint_watch/.test(job.prompt_preview ?? job.prompt ?? '')))
  const resolved = candidates.map(job => {
    const id = WATCH_IN_PROMPT.exec(job.prompt_preview ?? job.prompt ?? '')?.[1] ?? null
    if (id) claimed.add(id)
    return { job, id }
  })
  for (const entry of resolved) {
    if (entry.id) continue
    const haystack = `${entry.job.name} ${entry.job.prompt_preview ?? ''}`.toLowerCase()
    const match = records.find(r => !claimed.has(r.id) && r.label && haystack.includes(String(r.label).toLowerCase()))
    if (match) {
      entry.id = match.id
      claimed.add(match.id)
    }
  }
  return resolved.map(({ job, id }) => {
      const record = id ? byId.get(id) : null
      return {
        jobId: job.job_id,
        id,
        label: job.name.startsWith(JOB_PREFIX) ? job.name.slice(JOB_PREFIX.length) : job.name,
        schedule: job.schedule,
        deliver: job.deliver,
        enabled: job.enabled !== false && job.state !== 'paused',
        lastRun: job.last_run_at,
        lastStatus: job.last_status,
        nextRun: job.next_run_at,
        error: job.last_delivery_error || job.last_error || null,
        area: record?.area ?? null,
        checks: record?.checks ?? 0,
        matchingNow: record?.matching_now ?? null,
        alerts: record?.alerts ?? []
      }
    })
}

export async function setWatchEnabled({ host }, watch, enabled) {
  try {
    await host.request('cron.manage', { action: enabled ? 'resume' : 'pause', name: watch.jobId })
    return { ok: true }
  } catch (e) {
    return { ok: false, error: errorText(e) }
  }
}

export async function removeWatch({ host, rest }, watch) {
  try {
    await host.request('cron.manage', { action: 'remove', name: watch.jobId })
  } catch (e) {
    return { ok: false, error: errorText(e) }
  }
  if (watch.id) await rest(`/watches/${watch.id}/forget`, { method: 'POST' }).catch(() => null)
  return { ok: true }
}
