// The globe: three.js scene, camera controls, an instanced marker pool for every layer, a track
// line for the selected moving object, country heat, and picking. It knows nothing about React or
// the plugin SDK; the page drives it through the returned handle and must call dispose().
import {
  BufferAttribute,
  BufferGeometry,
  DataTexture,
  DoubleSide,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  Line,
  LinearFilter,
  Mesh,
  NearestFilter,
  NormalBlending,
  PerspectiveCamera,
  PlaneGeometry,
  RedFormat,
  Scene,
  ShaderMaterial,
  SphereGeometry,
  UnsignedByteType,
  Vector2,
  Vector3,
  WebGLRenderer
} from 'three'
import { countryRaster, indexAt, RASTER_HEIGHT, RASTER_WIDTH } from '../geo/world.js'
import {
  backdropFragment,
  backdropVertex,
  earthFragment,
  earthVertex,
  markerFragment,
  markerVertex,
  pathFragment,
  pathVertex
} from './shaders.js'

export const MARKER_POOL = 8192
export const MAX_LAYERS = 12
const PATH_POINTS = 256
const FOV = 30
const DEG = Math.PI / 180

export function toVector(lat, lon, out = new Vector3()) {
  const phi = lat * DEG
  const lambda = lon * DEG
  return out.set(Math.cos(phi) * Math.sin(lambda), Math.sin(phi), Math.cos(phi) * Math.cos(lambda))
}

export function toLatLon(v) {
  const n = v.clone().normalize()
  return { lat: Math.asin(Math.max(-1, Math.min(1, n.y))) / DEG, lon: Math.atan2(n.x, n.z) / DEG }
}

const EARTH_KM = 6371
const easeInOut = t => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2)
const wrapLon = lon => ((((lon + 180) % 360) + 360) % 360) - 180

/**
 * A marker the page hands to setMarkers().
 * @typedef {{ id: string, lat: number, lon: number, altKm?: number, shape: number, color: number,
 *   size: number, heading?: number, alpha: number, layer: number }} Marker
 *
 * @param {HTMLCanvasElement} canvas
 * @param {object} options
 * @param {(target: object|null, x: number, y: number) => void} options.onHover
 * @param {(target: object) => void} options.onPick       a marker, country or ocean point was clicked
 * @param {(state: object) => void} options.onFrame       per-frame tracked screen position
 * @param {(view: object) => void} options.onView         camera moved (throttled by the caller)
 * @param {() => void} options.onContextLost
 * @param {(fn: (now: number) => void, opts: object) => {wake(): void, dispose(): void}} options.loop
 */
export function createGlobe(canvas, options) {
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, premultipliedAlpha: true, powerPreference: 'high-performance' })
  renderer.setClearColor(0x000000, 0)
  const scene = new Scene()
  const camera = new PerspectiveCamera(FOV, 1, 0.005, 20) // near plane inside the closest zoom (0.035 above the surface)
  const size = new Vector2(1, 1)
  let pixelRatio = 1

  // --- Textures -------------------------------------------------------------------------------
  const idTexture = new DataTexture(countryRaster(), RASTER_WIDTH, RASTER_HEIGHT, RedFormat, UnsignedByteType)
  idTexture.minFilter = NearestFilter
  idTexture.magFilter = NearestFilter
  idTexture.generateMipmaps = false
  idTexture.flipY = false
  idTexture.needsUpdate = true
  const heatBytes = new Uint8Array(256)
  const heatTexture = new DataTexture(heatBytes, 256, 1, RedFormat, UnsignedByteType)
  heatTexture.minFilter = LinearFilter
  heatTexture.magFilter = NearestFilter
  heatTexture.needsUpdate = true

  // --- Backdrop -------------------------------------------------------------------------------
  const backdropUniforms = {
    uCenter: { value: new Vector2() },
    uRadius: { value: 100 },
    uGlow: { value: new Vector3(0.5, 0.6, 1) },
    uStar: { value: new Vector3(1, 1, 1) },
    uTime: { value: 0 },
    uDither: { value: 1 },
    uCell: { value: 2 },
    uDark: { value: 1 },
    uMotion: { value: 1 }
  }
  const backdrop = new Mesh(
    new PlaneGeometry(2, 2),
    new ShaderMaterial({
      vertexShader: backdropVertex,
      fragmentShader: backdropFragment,
      uniforms: backdropUniforms,
      depthTest: false,
      depthWrite: false,
      transparent: true,
      premultipliedAlpha: true
    })
  )
  backdrop.frustumCulled = false
  backdrop.renderOrder = 0
  scene.add(backdrop)

  // --- Earth ----------------------------------------------------------------------------------
  const earthUniforms = {
    uIds: { value: idTexture },
    uHeat: { value: heatTexture },
    uIdSize: { value: [RASTER_WIDTH, RASTER_HEIGHT] },
    uHover: { value: -1 },
    uSelected: { value: -1 },
    uFocus: { value: 0 },
    uDither: { value: 1 },
    uCell: { value: 2 },
    uDark: { value: 1 },
    uLight: { value: new Vector3(-0.55, 0.6, 0.8) },
    uOcean: { value: new Vector3() },
    uOceanLit: { value: new Vector3() },
    uLandLow: { value: new Vector3() },
    uLand: { value: new Vector3() },
    uAccent: { value: new Vector3() },
    uHeatColor: { value: new Vector3() },
    uRim: { value: new Vector3() }
  }
  const earth = new Mesh(
    new SphereGeometry(1, 160, 96),
    new ShaderMaterial({ vertexShader: earthVertex, fragmentShader: earthFragment, uniforms: earthUniforms })
  )
  earth.renderOrder = 1
  scene.add(earth)

  // --- Markers --------------------------------------------------------------------------------
  const markerGeometry = new InstancedBufferGeometry()
  {
    const quad = new PlaneGeometry(1, 1)
    markerGeometry.index = quad.index
    markerGeometry.setAttribute('position', quad.getAttribute('position'))
  }
  const mPos = new InstancedBufferAttribute(new Float32Array(MARKER_POOL * 3), 3)
  const mStyle = new InstancedBufferAttribute(new Float32Array(MARKER_POOL * 4), 4)
  const mMeta = new InstancedBufferAttribute(new Float32Array(MARKER_POOL * 4), 4)
  markerGeometry.setAttribute('aPos', mPos)
  markerGeometry.setAttribute('aStyle', mStyle)
  markerGeometry.setAttribute('aMeta', mMeta)
  markerGeometry.instanceCount = 0
  const colorUniform = Array.from({ length: MAX_LAYERS }, () => new Vector3(1, 1, 1))
  const markerUniforms = {
    uRes: { value: size },
    uPixelRatio: { value: 1 },
    uTime: { value: 0 },
    uMask: { value: new Array(MAX_LAYERS).fill(1) },
    uZoom: { value: 1 },
    uColors: { value: colorUniform },
    uInk: { value: new Vector3(1, 1, 1) },
    uDark: { value: 1 },
    uStatic: { value: 0 }
  }
  const markerMaterial = new ShaderMaterial({
    vertexShader: markerVertex,
    fragmentShader: markerFragment,
    uniforms: markerUniforms,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: NormalBlending,
    premultipliedAlpha: true,
    // Billboards are built in screen space, so their winding is arbitrary: never cull them.
    side: DoubleSide
  })
  const markers = new Mesh(markerGeometry, markerMaterial)
  markers.frustumCulled = false
  markers.renderOrder = 3
  scene.add(markers)

  // --- Track line (selected satellite / aircraft) ------------------------------------------------
  const pathGeometry = new BufferGeometry()
  const pathPos = new Float32Array(PATH_POINTS * 3)
  const pathT = new Float32Array(PATH_POINTS)
  for (let i = 0; i < PATH_POINTS; i++) pathT[i] = i / (PATH_POINTS - 1)
  pathGeometry.setAttribute('position', new BufferAttribute(pathPos, 3))
  pathGeometry.setAttribute('aT', new BufferAttribute(pathT, 1))
  pathGeometry.setDrawRange(0, 0)
  const pathUniforms = { uColor: { value: new Vector3(1, 1, 1) } }
  const path = new Line(
    pathGeometry,
    new ShaderMaterial({
      vertexShader: pathVertex,
      fragmentShader: pathFragment,
      uniforms: pathUniforms,
      transparent: true,
      depthWrite: false,
      premultipliedAlpha: true
    })
  )
  path.frustumCulled = false
  path.renderOrder = 2
  scene.add(path)

  // --- State ----------------------------------------------------------------------------------
  let lastNow = null
  let clock = 0 // seconds since start (drives pulses)
  let reducedMotion = false
  let autoRotate = true
  let autoRotateHoldUntil = 0
  const view = { lat: 22, lon: 15, dist: 4.5 }
  let fitDist = 4.5
  const velocity = { lon: 0, lat: 0 }
  let tween = null
  let disposed = false
  let contextLost = false

  let markerList = [] // Marker[] in slot order
  const births = new Map() // id -> clock time first seen
  let selectedId = null
  let selectedIndex = -1
  let hoverIndex = -1
  let tracked = null // { lat, lon, altKm }
  let lastViewEmit = ''

  // --- Sizing ---------------------------------------------------------------------------------
  function resize(width, height) {
    if (disposed || width < 2 || height < 2) return
    pixelRatio = Math.min(window.devicePixelRatio || 1, 2)
    renderer.setPixelRatio(pixelRatio)
    renderer.setSize(width, height, false)
    size.set(width * pixelRatio, height * pixelRatio)
    camera.aspect = width / height
    // Fit: the globe's apparent radius is ~34% of the limiting dimension.
    const verticalTan = Math.tan((FOV / 2) * DEG)
    const ndcRadius = 0.68 * Math.min(1, camera.aspect)
    const oldFit = fitDist
    fitDist = Math.sqrt(1 + (1 / (ndcRadius * verticalTan)) ** 2)
    view.dist = Math.min(maxDist(), view.dist * (fitDist / oldFit))
    camera.updateProjectionMatrix()
    const cell = Math.max(1, Math.round(1.5 * pixelRatio))
    earthUniforms.uCell.value = cell
    backdropUniforms.uCell.value = cell
    markerUniforms.uPixelRatio.value = pixelRatio
    wake()
  }
  const minDist = () => 1.035 // close enough to separate individual cameras in a city
  const maxDist = () => fitDist * 1.45

  function globeRadiusPx() {
    const h = size.y / pixelRatio
    return h / 2 / (Math.sqrt(view.dist * view.dist - 1) * Math.tan((FOV / 2) * DEG))
  }

  // --- Palette --------------------------------------------------------------------------------
  function setPalette(p) {
    const set = (u, c) => u.value.set(c[0], c[1], c[2])
    set(earthUniforms.uOcean, p.ocean)
    set(earthUniforms.uOceanLit, p.oceanLit)
    set(earthUniforms.uLandLow, p.landLow)
    set(earthUniforms.uLand, p.land)
    set(earthUniforms.uAccent, p.accent)
    set(earthUniforms.uHeatColor, p.heat)
    set(earthUniforms.uRim, p.rim)
    set(backdropUniforms.uGlow, p.rim)
    set(backdropUniforms.uStar, p.star)
    set(markerUniforms.uInk, p.ink)
    set(pathUniforms.uColor, p.accent)
    p.layers.forEach((c, i) => {
      if (i < MAX_LAYERS) colorUniform[i].set(c[0], c[1], c[2])
    })
    const dark = p.dark ? 1 : 0
    earthUniforms.uDark.value = dark
    backdropUniforms.uDark.value = dark
    markerUniforms.uDark.value = dark
    wake()
  }

  // --- Markers --------------------------------------------------------------------------------
  const tmpA = new Vector3()

  function radiusFor(altKm) {
    // Surface objects sit just above the ground; altitude is exaggerated a little for aircraft
    // (otherwise 10 km is invisible) and compressed for high orbits (GPS at 20,000 km).
    if (!altKm || altKm <= 0) return 1.004
    if (altKm < 30) return 1.006 + altKm * 0.0016
    return 1 + Math.min(0.9, Math.log1p(altKm / 400) * 0.12 + 0.02)
  }

  /** Replace every marker. Cheap enough to call on each data change (and 1 Hz for satellites). */
  function setMarkers(list, { quietIds } = {}) {
    const n = Math.min(list.length, MARKER_POOL)
    markerList = list.length > n ? list.slice(0, n) : list
    const seen = new Set()
    for (let i = 0; i < n; i++) {
      const m = markerList[i]
      seen.add(m.id)
      if (!births.has(m.id)) births.set(m.id, quietIds?.has(m.id) ? -1e6 : clock)
      const v = toVector(m.lat, m.lon, tmpA).multiplyScalar(radiusFor(m.altKm))
      mPos.setXYZ(i, v.x, v.y, v.z)
      mStyle.setXYZW(i, m.shape, m.color, m.size, ((m.heading ?? 0) * Math.PI) / 180)
      mMeta.setXYZW(i, m.alpha, births.get(m.id), m.layer, m.id === selectedId ? 1 : 0)
    }
    if (births.size > n * 2 + 2000) for (const id of births.keys()) if (!seen.has(id)) births.delete(id)
    markerGeometry.instanceCount = n
    mPos.needsUpdate = mStyle.needsUpdate = mMeta.needsUpdate = true
    wake()
  }

  function setPath(points) {
    const n = Math.min(points?.length ?? 0, PATH_POINTS)
    for (let i = 0; i < n; i++) {
      const p = points[i]
      const v = toVector(p.lat, p.lon, tmpA).multiplyScalar(radiusFor(p.altKm))
      pathPos[i * 3] = v.x
      pathPos[i * 3 + 1] = v.y
      pathPos[i * 3 + 2] = v.z
      pathT[i] = n > 1 ? i / (n - 1) : 0
    }
    pathGeometry.attributes.position.needsUpdate = true
    pathGeometry.attributes.aT.needsUpdate = true
    pathGeometry.setDrawRange(0, n)
    wake()
  }

  // --- Camera ---------------------------------------------------------------------------------
  function applyCamera() {
    toVector(view.lat, view.lon, camera.position).multiplyScalar(view.dist)
    camera.up.set(0, 1, 0)
    camera.lookAt(0, 0, 0)
    camera.updateMatrixWorld()
  }

  function flyTo(lat, lon, dist = view.dist) {
    const target = { lat: Math.max(-75, Math.min(75, lat)), lon: wrapLon(lon), dist: Math.max(minDist(), Math.min(maxDist(), dist)) }
    velocity.lon = velocity.lat = 0
    autoRotateHoldUntil = performance.now() + 12000
    if (reducedMotion) {
      Object.assign(view, target)
      tween = null
    } else {
      let dLon = target.lon - view.lon
      if (dLon > 180) dLon -= 360
      if (dLon < -180) dLon += 360
      const angle = Math.hypot(dLon, target.lat - view.lat)
      tween = { from: { ...view }, dLon, to: target, start: performance.now(), ms: 700 + Math.min(900, angle * 6) }
    }
    wake()
  }

  function zoomBy(factor) {
    tween = null
    view.dist = Math.max(minDist(), Math.min(maxDist(), view.dist * factor))
    autoRotateHoldUntil = performance.now() + 8000
    wake()
  }

  function rotateBy(dLon, dLat) {
    tween = null
    view.lon = wrapLon(view.lon + dLon)
    view.lat = Math.max(-80, Math.min(80, view.lat + dLat))
    autoRotateHoldUntil = performance.now() + 8000
    wake()
  }

  // --- Pointer input --------------------------------------------------------------------------
  const pointers = new Map()
  let drag = null
  let pinch = null
  let hoverPoint = null
  let hoverDirty = false
  let lastHoverKey = ''
  let moved = 0

  function local(e) {
    const rect = canvas.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  function onPointerDown(e) {
    if (e.button !== 0 && e.pointerType === 'mouse') return
    canvas.setPointerCapture?.(e.pointerId)
    pointers.set(e.pointerId, local(e))
    tween = null
    velocity.lon = velocity.lat = 0
    moved = 0
    if (pointers.size === 1) drag = { ...local(e), t: performance.now() }
    else if (pointers.size === 2) {
      const [p, q] = [...pointers.values()]
      pinch = { d: Math.hypot(p.x - q.x, p.y - q.y), dist: view.dist }
      drag = null
    }
    autoRotateHoldUntil = performance.now() + 8000
    wake()
  }

  function onPointerMove(e) {
    const p = local(e)
    if (pointers.has(e.pointerId)) pointers.set(e.pointerId, p)
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()]
      view.dist = Math.max(minDist(), Math.min(maxDist(), pinch.dist * (pinch.d / Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)))))
      moved += 10
    } else if (drag) {
      const k = 1 / globeRadiusPx()
      const dx = p.x - drag.x
      const dy = p.y - drag.y
      moved += Math.abs(dx) + Math.abs(dy)
      const now = performance.now()
      const dt = Math.max(1, now - drag.t)
      const dLon = (-dx * k) / DEG
      const dLat = (dy * k) / DEG
      view.lon = wrapLon(view.lon + dLon)
      view.lat = Math.max(-80, Math.min(80, view.lat + dLat))
      velocity.lon = (dLon / dt) * 16
      velocity.lat = (dLat / dt) * 16
      drag = { x: p.x, y: p.y, t: now }
      autoRotateHoldUntil = now + 8000
    }
    hoverPoint = e.pointerType === 'touch' ? null : p
    hoverDirty = true
    wake()
  }

  function onPointerUp(e) {
    const wasClick = drag && moved < 5 && pointers.size === 1
    pointers.delete(e.pointerId)
    canvas.releasePointerCapture?.(e.pointerId)
    if (pointers.size < 2) pinch = null
    if (pointers.size === 0) {
      drag = null
      if (reducedMotion) velocity.lon = velocity.lat = 0
    }
    if (wasClick) {
      const target = pick(local(e))
      if (target) options.onPick?.(target)
    }
    wake()
  }

  function onPointerLeave() {
    hoverPoint = null
    hoverDirty = true
    wake()
  }

  function onWheel(e) {
    e.preventDefault()
    const delta = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY
    zoomBy(Math.exp(delta * 0.0012))
  }

  function onDoubleClick(e) {
    const hit = raycast(local(e))
    if (hit) flyTo(hit.lat, hit.lon, view.dist * 0.6)
  }

  canvas.addEventListener('pointerdown', onPointerDown)
  canvas.addEventListener('pointermove', onPointerMove)
  canvas.addEventListener('pointerup', onPointerUp)
  canvas.addEventListener('pointercancel', onPointerUp)
  canvas.addEventListener('pointerleave', onPointerLeave)
  canvas.addEventListener('wheel', onWheel, { passive: false })
  canvas.addEventListener('dblclick', onDoubleClick)

  function onContextLost(e) {
    e.preventDefault()
    contextLost = true
    options.onContextLost?.()
  }
  function onContextRestored() {
    contextLost = false
    idTexture.needsUpdate = true
    heatTexture.needsUpdate = true
    wake()
  }
  canvas.addEventListener('webglcontextlost', onContextLost)
  canvas.addEventListener('webglcontextrestored', onContextRestored)

  // --- Picking --------------------------------------------------------------------------------
  const ray = new Vector3()
  const origin = new Vector3()
  const projected = new Vector3()
  let mask = new Array(MAX_LAYERS).fill(true)

  function raycast({ x, y }) {
    const w = size.x / pixelRatio
    const h = size.y / pixelRatio
    ray.set((x / w) * 2 - 1, -(y / h) * 2 + 1, 0.5).unproject(camera).sub(camera.position).normalize()
    origin.copy(camera.position)
    const b = origin.dot(ray)
    const c = origin.lengthSq() - 1
    const disc = b * b - c
    if (disc < 0) return null
    const t = -b - Math.sqrt(disc)
    if (t < 0) return null
    return toLatLon(origin.addScaledVector(ray, t))
  }

  /** Screen position of a world point, or null when the globe hides it. */
  function screenOfWorld(v) {
    // Hidden if the segment camera→point passes through the unit sphere.
    const cam = camera.position
    const d = v.clone().sub(cam)
    const len = d.length()
    d.divideScalar(len)
    const b = cam.dot(d)
    const disc = b * b - (cam.lengthSq() - 1)
    if (disc > 0) {
      const t = -b - Math.sqrt(disc)
      if (t > 0 && t < len - 1e-3) return null
    }
    projected.copy(v).project(camera)
    return { x: ((projected.x + 1) / 2) * (size.x / pixelRatio), y: ((1 - projected.y) / 2) * (size.y / pixelRatio) }
  }

  function screenOf(lat, lon, altKm = 0) {
    return screenOfWorld(toVector(lat, lon, new Vector3()).multiplyScalar(radiusFor(altKm)))
  }

  function pick(point) {
    let best = -1
    let bestD = 13
    const v = new Vector3()
    for (let i = 0; i < markerList.length; i++) {
      const m = markerList[i]
      if (!mask[m.layer] || m.alpha <= 0) continue
      v.fromBufferAttribute(mPos, i)
      // Cheap reject: far side of the globe.
      if (v.dot(camera.position) < 0.2) continue
      const at = screenOfWorld(v)
      if (!at) continue
      const d = Math.hypot(at.x - point.x, at.y - point.y) - m.size * 0.35
      if (d < bestD) {
        bestD = d
        best = i
      }
    }
    if (best >= 0) return { kind: 'marker', id: markerList[best].id }
    const hit = raycast(point)
    if (!hit) return null
    const index = indexAt(hit.lat, hit.lon)
    if (index > 0 && index < 255) return { kind: 'country', index, lat: hit.lat, lon: hit.lon }
    return { kind: 'ocean', lat: hit.lat, lon: hit.lon }
  }

  // --- Frame ----------------------------------------------------------------------------------
  function frame(now) {
    if (disposed || contextLost) return
    const dt = lastNow === null ? 0 : Math.min(0.1, (now - lastNow) / 1000)
    lastNow = now
    clock += dt

    if (tween) {
      const p = Math.min(1, (now - tween.start) / tween.ms)
      const e = easeInOut(p)
      view.lon = wrapLon(tween.from.lon + tween.dLon * e)
      view.lat = tween.from.lat + (tween.to.lat - tween.from.lat) * e
      view.dist = tween.from.dist + (tween.to.dist - tween.from.dist) * e
      if (p >= 1) tween = null
    } else if (!drag && !pinch) {
      if (Math.abs(velocity.lon) > 0.001 || Math.abs(velocity.lat) > 0.001) {
        view.lon = wrapLon(view.lon + velocity.lon * dt * 60)
        view.lat = Math.max(-80, Math.min(80, view.lat + velocity.lat * dt * 60))
        const decay = Math.exp(-dt * 3.2)
        velocity.lon *= decay
        velocity.lat *= decay
      } else if (autoRotate && !reducedMotion && now > autoRotateHoldUntil) {
        view.lon = wrapLon(view.lon + dt * 1.6)
      }
    }
    applyCamera()

    markerUniforms.uTime.value = clock
    // Markers grow a little as you zoom in, so dense areas separate.
    markerUniforms.uZoom.value = Math.min(1.7, Math.max(0.85, Math.sqrt(fitDist / view.dist)))
    backdropUniforms.uTime.value = now / 1000
    backdropUniforms.uRadius.value = globeRadiusPx() * pixelRatio
    backdropUniforms.uCenter.value.set(size.x / 2, size.y / 2)
    earthUniforms.uSelected.value = selectedIndex
    earthUniforms.uHover.value = hoverIndex

    renderer.render(scene, camera)

    if (hoverDirty) {
      hoverDirty = false
      const target = hoverPoint && !drag && !pinch ? pick(hoverPoint) : null
      const key = target ? (target.kind === 'marker' ? target.id : target.kind === 'country' ? `c${target.index}` : 'ocean') : ''
      hoverIndex = target?.kind === 'country' ? target.index : -1
      if (key !== lastHoverKey || target) {
        lastHoverKey = key
        options.onHover?.(target && target.kind !== 'ocean' ? target : null, hoverPoint?.x ?? 0, hoverPoint?.y ?? 0)
      }
    }
    if (tracked) options.onFrame?.({ tracked: screenOf(tracked.lat, tracked.lon, tracked.altKm) })
    const viewKey = `${view.lat.toFixed(1)},${view.lon.toFixed(1)},${view.dist.toFixed(2)}`
    if (viewKey !== lastViewEmit) {
      lastViewEmit = viewKey
      options.onView?.({ ...view, fit: fitDist })
    }
  }

  function idle() {
    if (tween || drag || pinch || hoverDirty) return false
    if (Math.abs(velocity.lon) > 0.001 || Math.abs(velocity.lat) > 0.001) return false
    if (autoRotate && !reducedMotion) return false
    // Pulses on newly arrived markers need frames for 2.4 s.
    for (const t of births.values()) if (clock - t < 2.5) return false
    return true
  }

  const loop = options.loop(frame, { fps: 60, idleWhen: idle })
  function wake() {
    if (!disposed) loop.wake()
  }

  applyCamera()

  return {
    resize,
    setPalette,
    setMarkers,
    setPath,
    wake,
    flyTo,
    zoomBy,
    rotateBy,
    screenOf,
    resetView() {
      flyTo(22, view.lon, fitDist)
    },
    view: () => ({ ...view, fit: fitDist }),
    /** Per-country heat 0..1, indexed by raster index (a Float32Array or array of 256). */
    setHeat(values) {
      for (let i = 0; i < 256; i++) heatBytes[i] = Math.round(Math.max(0, Math.min(1, values[i] || 0)) * 255)
      heatTexture.needsUpdate = true
      wake()
    },
    focusCountry(country) {
      selectedIndex = country ? country.index : -1
      earthUniforms.uFocus.value = country ? 1 : 0
      tracked = country ? { lat: country.lat, lon: country.lon, altKm: 0 } : null
      if (country) flyTo(country.lat, country.lon, Math.max(1.9, 1.55 + Math.sqrt(country.area) * 4.6))
      wake()
    },
    /** Highlight a marker (or none) and optionally fly to it. */
    select(marker, { fly = true } = {}) {
      selectedId = marker ? marker.id : null
      for (let i = 0; i < markerList.length; i++) mMeta.setW(i, markerList[i].id === selectedId ? 1 : 0)
      mMeta.needsUpdate = true
      if (marker && fly) flyTo(marker.lat, marker.lon, Math.min(view.dist, Math.max(2.2, fitDist * 0.62)))
      if (!marker) setPath([])
      wake()
    },
    track(point) {
      tracked = point
      wake()
    },
    setMask(next) {
      mask = next.slice(0, MAX_LAYERS)
      while (mask.length < MAX_LAYERS) mask.push(true)
      markerUniforms.uMask.value = mask.map(on => (on ? 1 : 0))
      wake()
    },
    setAutoRotate(value) {
      autoRotate = value
      autoRotateHoldUntil = 0
      wake()
    },
    setDither(value) {
      earthUniforms.uDither.value = value ? 1 : 0
      backdropUniforms.uDither.value = value ? 1 : 0
      wake()
    },
    setReducedMotion(value) {
      reducedMotion = value
      markerUniforms.uStatic.value = value ? 1 : 0
      backdropUniforms.uMotion.value = value ? 0 : 1
      wake()
    },
    stats() {
      return {
        markers: markerGeometry.instanceCount,
        view: { ...view },
        drawCalls: renderer.info.render.calls,
        triangles: renderer.info.render.triangles,
        geometries: renderer.info.memory.geometries,
        textures: renderer.info.memory.textures,
        dormant: typeof loop.isDormant === 'function' ? loop.isDormant() : null
      }
    },
    dispose() {
      if (disposed) return
      disposed = true
      loop.dispose()
      canvas.removeEventListener('pointerdown', onPointerDown)
      canvas.removeEventListener('pointermove', onPointerMove)
      canvas.removeEventListener('pointerup', onPointerUp)
      canvas.removeEventListener('pointercancel', onPointerUp)
      canvas.removeEventListener('pointerleave', onPointerLeave)
      canvas.removeEventListener('wheel', onWheel)
      canvas.removeEventListener('dblclick', onDoubleClick)
      canvas.removeEventListener('webglcontextlost', onContextLost)
      canvas.removeEventListener('webglcontextrestored', onContextRestored)
      for (const obj of [backdrop, earth, markers, path]) {
        obj.geometry.dispose()
        obj.material.dispose()
      }
      idTexture.dispose()
      heatTexture.dispose()
      renderer.dispose()
      renderer.forceContextLoss()
    }
  }
}

export { EARTH_KM }
