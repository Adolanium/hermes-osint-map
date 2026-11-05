// Watches on the page: ids, and pairing cron jobs with what osint_watch remembers.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { joinWatches, watchId } from '../src/watch.js'

test('watch ids are readable slugs with a suffix', () => {
  assert.equal(watchId('Taiwan Strait', () => 0), 'taiwan-strait-0000')
  assert.match(watchId('!!!'), /^area-[a-z0-9]{4}$/)
})

test('page jobs match by id; chat-made jobs match by label; other jobs are ignored', () => {
  const records = [
    { id: 'moscow-x9ci', label: 'Moscow', area: { lat: 55.7 }, checks: 3, alerts: [] },
    { id: 'japan-quakes-m5', label: 'Japan', area: { country: 'JP' }, checks: 1, alerts: [] }
  ]
  const jobs = [
    { job_id: 'a', name: 'OSINT watch · Moscow', prompt_preview: 'OSINT Map watch (watch_id="moscow-x9ci"). Call' },
    { job_id: 'b', name: 'Japan M5+ earthquake watch', prompt_preview: 'You are a silent earthquake monitor for Japan. Call the osint_watch tool exactly once' },
    { job_id: 'c', name: 'Morning digest', prompt_preview: 'Summarize my inbox' }
  ]
  const watches = joinWatches(jobs, records)
  assert.deepEqual(watches.map(w => [w.jobId, w.id, w.label]), [
    ['a', 'moscow-x9ci', 'Moscow'],
    ['b', 'japan-quakes-m5', 'Japan M5+ earthquake watch']
  ])
  assert.equal(watches[1].area.country, 'JP')
})

test('the big camera view steps through the nearest cameras, the opened one first', async () => {
  const { nearestCameras } = await import('../src/data/cameras.js')
  const cam = (id, lat, lon, extra = {}) => ({ id, layer: 'cameras', lat, lon, ...extra })
  const items = [cam('far', 35.0, -118.0), cam('near', 34.06, -118.24), cam('origin', 34.05, -118.25), cam('cluster', 34.05, -118.25, { cluster: true }), { id: 'q', layer: 'quakes', lat: 34, lon: -118 }]
  assert.deepEqual(nearestCameras(items, items[2]).map(i => i.id), ['origin', 'near', 'far'])
})
