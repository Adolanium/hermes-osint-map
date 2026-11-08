// Camera pool selection: the wall and the arrow-stepping list must stay inside the operator's own
// feed. Singapore's LTA feed publishes eight cameras while the wall shows nine tiles, and Hong Kong
// is the nearest other camera city (~2,500 km away), so any unfiltered pool pads a Singapore wall
// with Hong Kong stills as soon as a tile fails. Fixtures are the real cameras both feeds listed.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { cameraSource, nearestCameras, POOL_SIZE, sameOperator, wallCameras } from '../src/data/cameras.js'
import { clusterItems, toItem } from '../src/data/layers.js'

const WALL_SIZE = 9 // what CameraWall renders; kept here so this file needs no JSX

const SUBTITLES = {
  SG: 'Land Transport Authority, Singapore',
  HK: 'Transport Department, Hong Kong',
  GB: 'Transport for London',
  US: 'Caltrans',
}

const camera = (source, key, lat, lon, country, title) => toItem({
  id: `cameras:${source}:${key}`,
  layer: 'cameras',
  kind: 'traffic',
  lat,
  lon,
  country,
  title: title ?? `${source} ${key}`,
  subtitle: SUBTITLES[country],
}, 'cameras')

// data.gov.sg/v1/transport/traffic-images, 2026-11-08: eight cameras, all eight stills answered 403.
const SINGAPORE = [
  ['2701', 1.447023728, 103.7716543],
  ['2702', 1.445554109, 103.7683397],
  ['2704', 1.429588536, 103.769311],
  ['4703', 1.348697862, 103.6350413],
  ['4712', 1.341244001, 103.6439134],
  ['4713', 1.347645829, 103.6366955],
  ['4798', 1.25999999687243, 103.823611110166],
  ['4799', 1.26027777363278, 103.823888890049],
].map(([key, lat, lon]) => camera('sg', key, lat, lon, 'SG', `Singapore traffic camera ${key}`))

// The Hong Kong cameras nearest Singapore: North Lantau Highway, ~2,585 km away.
const HONG_KONG = [
  ['AID08122', 22.290998588, 113.940659915, 'North Lantau Highway near Tung Chung (N) - Westbound'],
  ['AID08203', 22.28960394, 113.944184756, 'North Lantau Highway near Tung Chung (N) - Eastbound'],
  ['AID08121', 22.28950534, 113.944699223, 'North Lantau Highway near Tung Chung (N) - Westbound'],
  ['NL128F', 22.28946, 113.94729, 'North Lantau Highway near Tung Chung'],
  ['AID08204', 22.289736772, 113.948696832, 'North Lantau Highway near Caribbean Coast Phase 3'],
  ['AID08120', 22.290244116, 113.949860477, 'North Lantau Highway near Caribbean Coast Phase 3'],
  ['AID08205', 22.29210812, 113.952526075, 'North Lantau Highway near Caribbean Coast Phase 1'],
  ['AID08119', 22.292478855, 113.952874829, 'North Lantau Highway near Caribbean Coast Phase 1'],
  ['AID08118', 22.294631457, 113.95529745, 'North Lantau Highway near Ying Tung Estate - Westbound'],
].map(([key, lat, lon, title]) => camera('hk', key, lat, lon, 'HK', title))

const LONDON = [
  ['00002.00865', 51.60067, -0.01594],
  ['00002.00866', 51.601, -0.016],
  ['00002.00867', 51.602, -0.018],
].map(([key, lat, lon]) => camera('tfl', key, lat, lon, 'GB', `A406 Billet Upass E ${key}`))

const CALIFORNIA = [
  ['d7:i110196', 34.0837, -118.2215],
  ['d7:i110197', 34.0839, -118.2217],
].map(([key, lat, lon]) => camera('ca', key, lat, lon, 'US', `I-110 : Avenue 26 Off Ramp ${key}`))

/** The real layer list: every operator's cameras in one array, exactly as layerItems('cameras') returns. */
const EVERY_CAMERA = [...SINGAPORE, ...HONG_KONG, ...LONDON, ...CALIFORNIA]

/** The Singapore cluster as clusterItems() builds it, passed straight to the wall. */
function singaporeCluster() {
  const { clusters } = clusterItems(EVERY_CAMERA)
  const found = [...clusters.values()].find(c => c.country === 'SG')
  assert.ok(found, 'the eight Singapore cameras form one cluster')
  return found
}

test('a Singapore cluster never pulls in another country\'s cameras', () => {
  const pool = wallCameras(EVERY_CAMERA, singaporeCluster())
  assert.deepEqual(pool.map(i => i.country), Array(8).fill('SG'))
  assert.ok(pool.every(i => i.id.startsWith('cameras:sg:')), 'only data.gov.sg cameras')
  assert.ok(!pool.some(i => i.id.startsWith('cameras:hk:')), 'no Hong Kong cameras')
  assert.equal(pool.length, 8, 'the pool is the operator\'s own feed, short as it is')
})

test('the pool is ordered by distance from the cluster centre, nearest first', () => {
  const cluster = singaporeCluster()
  const pool = wallCameras(EVERY_CAMERA, cluster)
  assert.equal(pool[0].id, 'cameras:sg:2704', 'the camera nearest the cluster mean comes first')
  const distance = i => (i.lat - cluster.lat) ** 2 + (((((i.lon - cluster.lon + 540) % 360) - 180) * Math.cos((cluster.lat * Math.PI) / 180)) ** 2)
  for (let n = 1; n < pool.length; n++) assert.ok(distance(pool[n]) >= distance(pool[n - 1]))
})

test('with every Singapore still failing, the wall shows nothing instead of Hong Kong images', () => {
  // What CameraWall does: take the pool, drop the tiles that reported an error, keep nine.
  const pool = wallCameras(EVERY_CAMERA, singaporeCluster())
  const down = new Set(SINGAPORE.map(i => i.id)) // all eight stills answered 403 from images.data.gov.sg
  const shown = pool.filter(i => !down.has(i.id)).slice(0, WALL_SIZE)
  assert.equal(shown.length, 0, 'a failing operator degrades to an empty wall, not to another country')
  assert.ok(!shown.some(i => i.country === 'HK'))
})

test('a healthy operator still fills its wall from its own cameras', () => {
  const pool = wallCameras(EVERY_CAMERA, { lat: 51.6, lon: -0.016, country: 'GB' })
  assert.equal(pool.length, 3)
  assert.ok(pool.every(i => i.id.startsWith('cameras:tfl:')), 'London keeps its London cameras')
})

test('the wall skips cluster markers, which have no frame to fetch', () => {
  const { items } = clusterItems(EVERY_CAMERA)
  const marker = items.find(i => i.cluster)
  assert.ok(marker, 'clustering produced a marker')
  const pool = wallCameras(items, singaporeCluster())
  assert.ok(!pool.some(i => i.cluster || i.id.includes(':cluster:')))
})

test('arrow-stepping stays inside the origin camera\'s feed', () => {
  const origin = SINGAPORE[0]
  const list = nearestCameras(EVERY_CAMERA, origin)
  assert.equal(list[0].id, origin.id, 'the origin comes first')
  assert.ok(list.every(i => i.country === 'SG'), 'no Hong Kong camera is ever one arrow away from Singapore')
  assert.ok(!list.some(i => i.id.startsWith('cameras:hk:')))
})

test('arrow-stepping does not jump from London to California or Hong Kong', () => {
  const list = nearestCameras(EVERY_CAMERA, LONDON[1])
  assert.deepEqual(list.map(i => i.country), ['GB', 'GB', 'GB'])
})

test('clusters carry their operator so the wall can scope to it', () => {
  const { clusters, items } = clusterItems(EVERY_CAMERA)
  const singapore = [...clusters.values()].find(c => c.country === 'SG')
  assert.equal(singapore.count, 8)
  assert.equal(singapore.country, 'SG')
  const hongKong = [...clusters.values()].find(c => c.country === 'HK')
  assert.equal(hongKong.country, 'HK')
  // Two operators in one country would still be told apart by the source in the id.
  assert.equal(cameraSource(items.find(i => i.cluster)), null, 'a cluster marker has no source')
})

test('items without a country are compared by the source segment of their id', () => {
  const bare = { id: 'cameras:sg:9999', layer: 'cameras', lat: 1.3, lon: 103.8, country: null }
  const hk = { id: 'cameras:hk:H999', layer: 'cameras', lat: 22.3, lon: 114.1, country: null }
  const center = { id: 'cameras:sg:2712', lat: 1.3, lon: 103.8, country: null } // a hand-built cluster
  assert.equal(sameOperator(bare, hk), false)
  assert.equal(sameOperator(bare, { ...bare, id: 'cameras:sg:1000' }), true)
  assert.equal(sameOperator(bare, center), true)
  assert.deepEqual(wallCameras([bare, hk, ...SINGAPORE], center).map(i => i.id)[0], 'cameras:sg:9999')
})

test('an unknown operator matches only itself, never everything', () => {
  const orphan = { id: 'camera-without-a-source', layer: 'cameras', lat: 0, lon: 0, country: null }
  assert.equal(sameOperator(orphan, SINGAPORE[0]), false)
  assert.deepEqual(wallCameras([orphan, ...SINGAPORE], { lat: 0, lon: 0, country: null }), [])
})

test('the wall still looks at a pool larger than one screen of tiles', () => {
  assert.ok(POOL_SIZE > WALL_SIZE)
})
