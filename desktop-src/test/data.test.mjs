// Page-side data handling: item validation, marker styling and age fade, satellite propagation.
// Run with `npm test` (node --test).
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { ageAlpha, LAYER_INDEX, LAYERS, layerOf, SHAPES, toItem, toMarker } from '../src/data/layers.js'
import { bearing, buildSatellites, orbitPath, periodMinutes, positionAt } from '../src/data/satellites.js'

const raw = (over = {}) => ({
  id: 'quakes:us1',
  layer: 'quakes',
  kind: 'earthquake',
  lat: 35.1,
  lon: 139.2,
  title: 'M 5.8 - 32 km SE of Hachijo, Japan',
  severity: 0.7,
  time: 1_700_000_000_000,
  country: 'JP',
  url: 'https://earthquake.usgs.gov/x',
  props: [['Magnitude', '5.8']],
  ...over
})

test('every layer has a unique id, a known shape and a theme colour', () => {
  assert.equal(new Set(LAYERS.map(l => l.id)).size, LAYERS.length)
  for (const l of LAYERS) {
    assert.ok(l.shape in SHAPES, l.id)
    assert.match(l.cssVar, /^--ui-/)
  }
  // Shapes carry meaning without colour: each layer that shares a colour has its own shape.
  assert.equal(new Set(LAYERS.map(l => l.shape)).size, LAYERS.length)
})

test('toItem keeps a valid item and sanitizes the rest', () => {
  const item = toItem(raw(), 'quakes')
  assert.equal(item.id, 'quakes:us1')
  assert.equal(item.country, 'JP')
  assert.deepEqual(item.props, [['Magnitude', '5.8']])
  const messy = toItem(raw({ country: 'jp', url: 'javascript:alert(1)', severity: 7, title: '  ', props: [['a'], 'x', ['k', 5]] }), 'quakes')
  assert.equal(messy.country, null)
  assert.equal(messy.url, null)
  assert.equal(messy.severity, 0.3)
  assert.equal(messy.title, 'Untitled')
  assert.deepEqual(messy.props, [['k', '5']])
})

test('toItem rejects items it cannot place, unless they carry orbital elements', () => {
  assert.equal(toItem(raw({ lat: null }), 'quakes'), null)
  assert.equal(toItem(raw({ lat: 95 }), 'quakes'), null)
  assert.equal(toItem({ ...raw(), id: 5 }, 'quakes'), null)
  assert.equal(toItem(null, 'quakes'), null)
  assert.ok(toItem(raw({ lat: null, lon: null, omm: { OBJECT_NAME: 'X' } }), 'satellites'))
})

test('old events fade toward a floor but never disappear; ongoing layers never fade', () => {
  const quakes = layerOf('quakes')
  const now = 1_700_000_000_000
  assert.equal(ageAlpha(quakes, now, now), 1)
  const half = ageAlpha(quakes, now - quakes.fadeAfter / 2, now)
  assert.ok(half < 1 && half > 0.28)
  assert.equal(ageAlpha(quakes, now - quakes.fadeAfter * 10, now), 0.28)
  assert.equal(ageAlpha(layerOf('weather'), now - 1e12, now), 1)
})

test('toMarker sizes by severity and dims civil aircraft', () => {
  const now = 1_700_000_000_000
  const small = toMarker(toItem(raw({ severity: 0, time: now }), 'quakes'), now)
  const big = toMarker(toItem(raw({ severity: 1, time: now }), 'quakes'), now)
  assert.ok(big.size > small.size)
  assert.equal(big.layer, LAYER_INDEX.quakes)
  assert.equal(big.shape, SHAPES.circle)
  const mil = toMarker(toItem(raw({ id: 'aircraft:a', kind: 'military', heading: 90, time: null }), 'aircraft'), now)
  const civ = toMarker(toItem(raw({ id: 'aircraft:b', kind: 'civil', heading: 90, time: null }), 'aircraft'), now)
  assert.ok(civ.alpha < mil.alpha)
  assert.equal(mil.heading, 90)
  assert.equal(toMarker(toItem(raw({ lat: null, lon: null, omm: {} }), 'satellites'), now), null)
})

test('satellites propagate from CelesTrak elements (ISS fixture)', () => {
  const omm = JSON.parse(readFileSync(new URL('./fixtures/iss-omm.json', import.meta.url), 'utf8'))[0]
  const [sat] = buildSatellites([{ id: 'satellites:25544', omm }, { id: 'bad', omm: { nonsense: true } }, { id: 'none' }])
  assert.ok(sat)
  const epoch = new Date(`${omm.EPOCH}Z`)
  const p = positionAt(sat.satrec, epoch)
  assert.ok(p.altKm > 380 && p.altKm < 460, `ISS altitude ${p.altKm}`)
  assert.ok(Math.abs(p.lat) <= 52, 'ISS stays within its inclination')
  assert.ok(p.speedKms > 7.4 && p.speedKms < 7.9)
  const period = periodMinutes(sat.satrec)
  assert.ok(period > 90 && period < 95)
  assert.equal(orbitPath(sat.satrec, epoch, 30).length, 30)
})

test('bearing', () => {
  assert.equal(Math.round(bearing({ lat: 0, lon: 0 }, { lat: 1, lon: 0 })), 0)
  assert.equal(Math.round(bearing({ lat: 0, lon: 0 }, { lat: 0, lon: 1 })), 90)
  assert.equal(Math.round(bearing({ lat: 0, lon: 0 }, { lat: -1, lon: 0 })), 180)
})
