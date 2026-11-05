// The Cyber attacks layer on the page: attack validation and arc pacing.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createCyberSource, createPlayback, isDomestic, toEvent } from '../src/data/cyber.js'
import { countryByCode } from '../src/geo/world.js'

const LIVE = { idPrefix: 'live:x:', demo: false }
const raw = (over = {}) => ({
  seq: 1,
  type: 'exploit',
  name: 'Directory traversal',
  count: 2,
  src: { country: 'US', lat: 37.751, lon: -97.822 },
  dst: { country: 'BR', lat: -15.6, lon: -56.1 },
  received: 1_700_000_000_000,
  repeat_of: null,
  ...over
})

test('toEvent keeps a valid backend event', () => {
  const e = toEvent(raw(), LIVE)
  assert.equal(e.id, 'live:x:1')
  assert.equal(e.type, 'exploit')
  assert.equal(e.count, 2)
  assert.equal(e.src.located, 'exact')
  assert.equal(e.demo, false)
})

test('toEvent rejects payloads without a usable sequence or placement', () => {
  for (const bad of [null, 'x', 5, {}, raw({ seq: 0 }), raw({ seq: 1.5 }), raw({ src: {} }), raw({ dst: { country: 'zz' } })]) {
    assert.equal(toEvent(bad, LIVE), null)
  }
})

test('toEvent places an end with no coordinates at its country, and sanitizes the rest', () => {
  const e = toEvent(raw({ dst: { country: 'FR', lat: null, lon: 999 }, type: 'worm', count: -4, name: '  ' }), LIVE)
  assert.equal(e.dst.located, 'country')
  assert.equal(e.dst.lat, countryByCode('FR').lat)
  assert.equal(e.type, 'other')
  assert.equal(e.count, 1)
  assert.equal(e.name, 'Unnamed attack')
})

test('repeat_of must point backwards', () => {
  assert.equal(toEvent(raw({ seq: 5, repeat_of: 3 }), LIVE).repeatOf, 'live:x:3')
  assert.equal(toEvent(raw({ seq: 5, repeat_of: 9 }), LIVE).repeatOf, null)
})

test('playback spreads a batch over the poll interval', () => {
  const pb = createPlayback({ spreadMs: 1000, maxPerSecond: 100 })
  pb.push([1, 2, 3, 4], 0)
  assert.deepEqual(pb.due(0), [1])
  assert.deepEqual(pb.due(499), [2])
  assert.deepEqual(pb.due(1000), [3, 4])
})

test('playback skips visuals under a burst instead of lagging forever', () => {
  const pb = createPlayback({ maxPerSecond: 10, maxLagMs: 1000 })
  const skipped = pb.push(Array.from({ length: 100 }, (_, i) => i), 0)
  assert.ok(pb.size() <= 11)
  assert.equal(skipped + pb.size(), 100)
  assert.equal(pb.skipped(), skipped)
})

test('playback.delay shifts pending launches after a pause', () => {
  const pb = createPlayback({ spreadMs: 1000, maxPerSecond: 100 })
  pb.push(['a', 'b'], 0)
  pb.delay(5000)
  assert.deepEqual(pb.due(1000), [])
  assert.deepEqual(pb.due(6000), ['a', 'b'])
})

test('isDomestic', () => {
  assert.equal(isDomestic(toEvent(raw({ dst: { country: 'US', lat: 40, lon: -75 } }), LIVE)), true)
  assert.equal(isDomestic(toEvent(raw(), LIVE)), false)
})

test('the poller counts the first batch as backlog and follows the cursor', async () => {
  const calls = []
  const answers = [
    { ok: true, stream_id: 's1', state: 'live', seq: 30, events: Array.from({ length: 30 }, (_, i) => raw({ seq: i + 1 })) },
    { ok: true, stream_id: 's1', state: 'live', seq: 31, events: [raw({ seq: 31 })] }
  ]
  const batches = []
  let pending = null
  const source = createCyberSource({
    rest: async path => {
      calls.push(path)
      return answers.shift() ?? { ok: true, stream_id: 's1', state: 'live', seq: 31, events: [] }
    },
    schedule: fn => ((pending = fn), () => (pending = null)),
    onBatch: (events, info) => batches.push({ n: events.length, ...info }),
    onStatus: () => {}
  })
  source.start()
  await pending()
  await pending()
  assert.deepEqual(calls.slice(0, 2), ['/cyber/events?after=0&limit=500', '/cyber/events?after=30&limit=500'])
  assert.deepEqual(batches.map(b => [b.n, b.first, b.backlog]), [[30, true, true], [1, false, false]])
})
