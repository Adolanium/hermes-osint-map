// The OSINT Map page: a full-bleed globe with glass overlays.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Codicon, createBudgetedLoop, GlyphSpinner, haptic, Tip, useTheme, useValue } from '@hermes/plugin-sdk'
import { CAMERA_DETAIL_DIST, clusterItems, LAYERS, layerOf, toMarker } from '../data/layers.js'
import { orbitPath } from '../data/satellites.js'
import { countryAt, countryByCode, countryByIndex, countryName } from '../geo/world.js'
import { createGlobe } from '../globe/globe.js'
import { buildPalette, createColorResolver } from '../globe/palette.js'
import { usePrefersReducedMotion, useThrottled } from './kit.jsx'
import { CountryPanel, FeedCard, ItemPanel, LayersCard, MarketsCard, ReconDrawer, SpaceWeatherCard } from './panels.jsx'
import { CSS } from './styles.js'

const QUIET_LAYERS = new Set(['aircraft', 'satellites', 'chokepoints', 'cameras'])
const HEAT_LAYERS = new Set(['incidents', 'disasters', 'quakes'])

export function OsintPage({ controller, rest, ask, openExternal }) {
  const rootRef = useRef(null)
  const globeRef = useRef(null)
  const settings = useValue(controller.settings)
  const registry = useValue(controller.registry)
  const selection = useValue(controller.selection)
  const alerts = useValue(controller.alerts)
  useThrottled(controller.version.subscribe, controller.version.get, 400)
  const reduced = usePrefersReducedMotion()
  const [sizeClass, setSizeClass] = useState('wide')
  const [openRail, setOpenRail] = useState(null)
  const [recon, setRecon] = useState(false)
  const [contextLost, setContextLost] = useState(null)

  useEffect(() => controller.attach(), [controller])

  useEffect(() => {
    const el = rootRef.current
    if (!el) return undefined
    const ro = new ResizeObserver(([entry]) => {
      const w = entry.contentRect.width
      setSizeClass(w < 760 ? 'narrow' : w < 1180 ? 'compact' : 'wide')
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const g = globeRef.current
    if (!g) return
    g.setMask(LAYERS.map(l => !!settings.layers[l.id]))
    g.setDither(settings.dither)
    g.setAutoRotate(settings.autoRotate)
  }, [settings])
  useEffect(() => globeRef.current?.setReducedMotion(reduced), [reduced])

  const selectItem = useCallback(
    item => {
      if (!item) return
      controller.selection.set({ kind: 'item', id: item.id })
      if (sizeClass === 'narrow') setOpenRail('right')
    },
    [controller, sizeClass]
  )
  const selectCountry = useCallback(
    code => {
      if (!countryByCode(code)) return
      controller.selection.set({ kind: 'country', code })
      if (sizeClass === 'narrow') setOpenRail('right')
    },
    [controller, sizeClass]
  )
  const clearSelection = useCallback(() => controller.selection.set(null), [controller])

  const onPick = useCallback(
    target => {
      haptic?.('tap')
      if (target.kind === 'marker') selectItem(controller.item(target.id))
      else if (target.kind === 'country') {
        const c = countryByIndex(target.index)
        if (c) selectCountry(c.code)
      }
    },
    [controller, selectCountry, selectItem]
  )

  function onKeyDown(e) {
    if (e.target instanceof HTMLElement && e.target.closest('input, textarea, [contenteditable="true"]')) return
    if (e.metaKey || e.ctrlKey || e.altKey) return
    const g = globeRef.current
    const k = e.key
    let handled = true
    if (k >= '1' && k <= String(LAYERS.length)) controller.toggleLayer(LAYERS[Number(k) - 1].id)
    else if (k === 'Escape') {
      if (recon) setRecon(false)
      else if (selection) clearSelection()
      else if (openRail) setOpenRail(null)
      else handled = false
    } else if (k === '/') setRecon(true)
    else if (k === 'r' || k === 'R') g?.resetView()
    else if (k === 'a' || k === 'A') controller.setAutoRotate(!settings.autoRotate)
    else if (k === 'd' || k === 'D') controller.setDither(!settings.dither)
    else if (k === '+' || k === '=') g?.zoomBy(0.85)
    else if (k === '-' || k === '_') g?.zoomBy(1 / 0.85)
    else if (k === 'ArrowLeft') g?.rotateBy(-8, 0)
    else if (k === 'ArrowRight') g?.rotateBy(8, 0)
    else if (k === 'ArrowUp') g?.rotateBy(0, 6)
    else if (k === 'ArrowDown') g?.rotateBy(0, -6)
    else handled = false
    if (handled) e.preventDefault()
  }

  const selectedItem = selection?.kind === 'item' ? controller.item(selection.id) : null
  const selectedCountry = selection?.kind === 'country' ? selection.code : null
  const missing = registry.state === 'missing'

  return (
    <div ref={rootRef} className="hom-root" data-size={sizeClass} data-motion={reduced ? 'reduced' : 'full'} tabIndex={0} onKeyDown={onKeyDown} aria-label="OSINT map">
      <style>{CSS}</style>
      <GlobeView
        controller={controller}
        globeRef={globeRef}
        onPick={onPick}
        onContextLost={error => setContextLost({ message: error ? String(error.message ?? error) : null })}
        selectedItem={selectedItem}
        selectedCountry={selectedCountry}
      />
      <div className="hom-shade" aria-hidden="true" />

      <TopBar controller={controller} settings={settings} registry={registry} onBrief={() => ask('brief')} onRecon={() => setRecon(r => !r)} recon={recon} />

      <div className="hom-rail hom-rail--left" data-open={openRail === 'left'}>
        {recon ? (
          <ReconDrawer rest={rest} onClose={() => setRecon(false)} onAsk={(kind, target, summary) => ask('recon', kind, target, summary)} />
        ) : (
          <>
            <LayersCard controller={controller} settings={settings} registry={registry} />
            <SpaceWeatherCard rest={rest} />
            <MarketsCard rest={rest} />
          </>
        )}
      </div>

      <div className="hom-rail hom-rail--right" data-open={openRail === 'right'}>
        {selectedItem ? (
          <ItemPanel
            key={selectedItem.id}
            item={selectedItem}
            controller={controller}
            onClose={clearSelection}
            onCountry={selectCountry}
            onAsk={item => ask('item', item)}
            openExternal={openExternal}
          />
        ) : selectedCountry ? (
          <CountryPanel
            key={selectedCountry}
            code={selectedCountry}
            controller={controller}
            rest={rest}
            onClose={clearSelection}
            onItem={selectItem}
            onAsk={code => ask('country', code)}
            openExternal={openExternal}
          />
        ) : null}
        <FeedCard controller={controller} settings={settings} rest={rest} onItem={selectItem} openExternal={openExternal} alerts={alerts} />
      </div>

      <Dock
        controller={controller}
        settings={settings}
        narrow={sizeClass === 'narrow'}
        openRail={openRail}
        setOpenRail={setOpenRail}
        recon={recon}
        setRecon={setRecon}
        onReset={() => globeRef.current?.resetView()}
      />

      {sizeClass !== 'narrow' ? (
        <div className="hom-attrib">
          Data: USGS, NASA EONET, GDACS, NWS, adsb.lol (ODbL), CelesTrak, GDELT, NOAA SWPC, World Bank. Borders: Natural Earth.
        </div>
      ) : null}

      {missing ? (
        <div className="hom-center">
          <div className="hom-panel hom-dialog" role="alert">
            <h2>The map needs the plugin's backend</h2>
            <p>
              Every layer is fetched by this plugin's Python half, which isn't loaded. Add <code className="hom-mono">hermes-osint-map</code> to{' '}
              <code className="hom-mono">plugins.enabled</code> in config.yaml, then restart the gateway.
            </p>
            <div className="hom-dialog-actions">
              <button type="button" className="hom-btn hom-btn--primary" onClick={() => controller.refresh()}>
                Try again
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {registry.state === 'loading' ? (
        <div className="hom-listening hom-panel" role="status">
          <GlyphSpinner ariaLabel="Loading" />
          <span>Connecting to the data sources…</span>
        </div>
      ) : null}

      {contextLost ? (
        <div className="hom-center">
          <div className="hom-panel hom-dialog" role="alert">
            <h2>The globe can't draw right now</h2>
            <p>The graphics context was lost or WebGL is unavailable. Layers, the feed and recon keep working.</p>
            {contextLost.message ? <p className="hom-caption hom-mono">{contextLost.message}</p> : null}
          </div>
        </div>
      ) : null}
    </div>
  )
}

/** Owns the canvas and the three.js globe; mirrors controller data into markers. */
function GlobeView({ controller, globeRef, onPick, onContextLost, selectedItem, selectedCountry }) {
  const canvasRef = useRef(null)
  const tipRef = useRef(null)
  const calloutRef = useRef(null)
  const [hover, setHover] = useState(null)
  const { themeName, resolvedMode } = useThemeSafe()
  const version = useThrottled(controller.version.subscribe, controller.version.get, 250)
  const onPickRef = useRef(onPick)
  onPickRef.current = onPick
  const onLostRef = useRef(onContextLost)
  onLostRef.current = onContextLost
  const seenLayers = useRef(new Set())
  const [calloutText, setCalloutText] = useState(null)
  // Cameras cluster until you zoom in close; clusters are not items, so they live here.
  const [cameraDetail, setCameraDetail] = useState(false)
  const clustersRef = useRef(new Map())

  useEffect(() => {
    const canvas = canvasRef.current
    const resolver = createColorResolver(canvas.parentElement)
    let globe
    let lastView = 0
    try {
      globe = createGlobe(canvas, {
        loop: (fn, opts) => createBudgetedLoop(fn, opts),
        onPick: target => {
          const cluster = target.kind === 'marker' ? clustersRef.current.get(target.id) : null
          if (cluster) globeRef.current?.flyTo(cluster.lat, cluster.lon, 1.035)
          else onPickRef.current(target)
        },
        onContextLost: () => onLostRef.current?.(),
        onView: v => {
          setCameraDetail(v.dist < CAMERA_DETAIL_DIST)
          const now = performance.now()
          if (now - lastView > 250) {
            lastView = now
            controller.setView(v)
          }
        },
        onHover: (target, x, y) => {
          const el = tipRef.current
          if (el) {
            const w = canvas.clientWidth
            const left = x + 16 + 280 > w ? x - 16 - 280 : x + 16
            el.style.transform = `translate(${Math.max(8, left)}px, ${Math.max(8, y - 12)}px)`
          }
          setHover(prev => {
            const key = target ? (target.kind === 'marker' ? target.id : `c${target.index}`) : null
            const prevKey = prev ? (prev.kind === 'marker' ? prev.id : `c${prev.index}`) : null
            return key === prevKey ? prev : target
          })
          canvas.dataset.hover = String(!!target)
        },
        onFrame: ({ tracked }) => {
          const el = calloutRef.current
          if (!el) return
          if (!tracked) {
            el.style.opacity = '0'
            return
          }
          el.style.opacity = '1'
          el.style.transform = `translate(${tracked.x}px, ${tracked.y}px)`
        }
      })
    } catch (error) {
      console.error('[hermes-osint-map] could not start the globe', error)
      onLostRef.current?.(error)
      resolver.dispose()
      return undefined
    }
    globeRef.current = globe
    const s = controller.settings.get()
    globe.setMask(LAYERS.map(l => !!s.layers[l.id]))
    globe.setDither(s.dither)
    globe.setAutoRotate(s.autoRotate)
    globe.setPalette(buildPalette(resolver))
    const ro = new ResizeObserver(([entry]) => globe.resize(entry.contentRect.width, entry.contentRect.height))
    ro.observe(canvas)
    canvas.__homResolver = resolver
    // Diagnostics handle for validating the page over CDP; removed on unmount.
    window.__homGlobe = globe
    window.__homController = controller
    return () => {
      ro.disconnect()
      globe.dispose()
      resolver.dispose()
      globeRef.current = null
      if (window.__homGlobe === globe) {
        delete window.__homGlobe
        delete window.__homController
      }
    }
  }, [controller, globeRef])

  useEffect(() => {
    const globe = globeRef.current
    const resolver = canvasRef.current?.__homResolver
    if (!globe || !resolver) return undefined
    const id = requestAnimationFrame(() => globe.setPalette(buildPalette(resolver)))
    return () => cancelAnimationFrame(id)
  }, [themeName, resolvedMode, globeRef])

  // Data → markers and country heat.
  useEffect(() => {
    const globe = globeRef.current
    if (!globe) return
    const now = Date.now()
    const markers = []
    const quiet = new Set()
    const heat = new Float32Array(256)
    let clusters = new Map()
    for (const layer of LAYERS) {
      let list = controller.layerItems(layer.id)
      if (layer.id === 'cameras' && !cameraDetail) {
        const grouped = clusterItems(list)
        list = grouped.items
        clusters = grouped.clusters
      }
      const firstSight = list.length > 0 && !seenLayers.current.has(layer.id)
      if (list.length) seenLayers.current.add(layer.id)
      for (const item of list) {
        const m = toMarker(item, now)
        if (!m) continue
        markers.push(m)
        if (firstSight || QUIET_LAYERS.has(layer.id)) quiet.add(m.id)
        if (HEAT_LAYERS.has(layer.id) && controller.settings.get().layers[layer.id]) {
          const c = item.country ? countryByCode(item.country) : countryAt(item.lat, item.lon)
          if (c) heat[c.index] += (item.count ?? 1) * (0.4 + item.severity) * m.alpha
        }
      }
    }
    // Moving objects last, so the static events under them stay clickable underneath.
    markers.sort((a, b) => (QUIET_LAYERS.has(LAYERS[a.layer].id) ? 1 : 0) - (QUIET_LAYERS.has(LAYERS[b.layer].id) ? 1 : 0))
    clustersRef.current = clusters
    globe.setMarkers(markers, { quietIds: quiet })
    let max = 0
    for (let i = 0; i < 256; i++) max = Math.max(max, heat[i])
    // Heat marks hot spots only: relative to the busiest country, cut below a quarter of it.
    if (max > 0)
      for (let i = 0; i < 256; i++) {
        const r = heat[i] / max
        heat[i] = r < 0.25 ? 0 : 0.18 + 0.55 * Math.sqrt((r - 0.25) / 0.75)
      }
    globe.setHeat(heat)
  }, [version, controller, globeRef, cameraDetail])

  // Selection → highlight, camera, track line and callout.
  const selectedId = selectedItem?.id ?? null
  useEffect(() => {
    const globe = globeRef.current
    if (!globe) return
    if (selectedCountry) {
      globe.select(null)
      globe.focusCountry(countryByCode(selectedCountry) ?? null)
      setCalloutText(countryName(selectedCountry))
      return
    }
    globe.focusCountry(null)
    if (!selectedId) {
      globe.select(null)
      globe.track(null)
      setCalloutText(null)
      return
    }
    const item = controller.item(selectedId)
    if (!item || item.lat === null) return
    globe.select(toMarker(item, Date.now()))
    setCalloutText(item.title.length > 48 ? `${item.title.slice(0, 46)}…` : item.title)
  }, [selectedId, selectedCountry, controller, globeRef])

  // Moving selections: keep the callout and track current.
  useEffect(() => {
    const globe = globeRef.current
    if (!globe || !selectedId) return
    const item = controller.item(selectedId)
    if (!item || item.lat === null) return
    globe.track({ lat: item.lat, lon: item.lon, altKm: item.altKm ?? 0 })
    if (item.layer === 'satellites') {
      const sat = controller.satellite(item.id)
      if (sat) globe.setPath(orbitPath(sat.satrec, new Date(), 180))
    } else if (item.layer === 'aircraft') {
      const trail = controller.trail(item.id)
      globe.setPath(trail.length > 1 ? trail : [])
    } else globe.setPath([])
  }, [version, selectedId, controller, globeRef])

  const hoverCluster = hover?.kind === 'marker' ? clustersRef.current.get(hover.id) : null
  const hoverItem = hover?.kind === 'marker' && !hoverCluster ? controller.item(hover.id) : null
  const hoverCountry = hover?.kind === 'country' ? countryByIndex(hover.index) : null

  return (
    <>
      <canvas ref={canvasRef} className="hom-canvas" aria-label="Interactive globe. Drag to rotate, scroll to zoom, click a marker or a country for details." />
      <div ref={calloutRef} className="hom-callout" style={{ opacity: 0 }} aria-hidden="true">
        {calloutText ? (
          <>
            <span className="hom-callout-dot" />
            <span className="hom-callout-line" />
            <span className="hom-callout-tag">{calloutText}</span>
          </>
        ) : null}
      </div>
      <div ref={tipRef} className="hom-tip hom-panel" style={{ opacity: hoverItem || hoverCountry || hoverCluster ? 1 : 0 }} aria-hidden="true">
        {hoverCluster ? (
          <>
            <span className="hom-tip-title">{hoverCluster.label}</span>
            <span className="hom-muted" style={{ fontSize: 10.5 }}>
              Click to zoom in
            </span>
          </>
        ) : hoverItem ? (
          <>
            <span className="hom-tip-title">{hoverItem.title}</span>
            <span className="hom-muted">
              {layerOf(hoverItem.layer).label}
              {hoverItem.subtitle ? ` · ${hoverItem.subtitle}` : ''}
            </span>
          </>
        ) : hoverCountry ? (
          <>
            <span className="hom-tip-title">
              <span className="hom-code">{hoverCountry.code}</span> {countryName(hoverCountry.code)}
            </span>
            <span className="hom-muted" style={{ fontSize: 10.5 }}>
              Click for a dossier
            </span>
          </>
        ) : null}
      </div>
    </>
  )
}

function useThemeSafe() {
  try {
    const t = useTheme()
    return { themeName: t?.themeName, resolvedMode: t?.resolvedMode }
  } catch {
    return { themeName: null, resolvedMode: null }
  }
}

function UtcClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <span className="hom-clock hom-mono hom-num" title="Coordinated Universal Time">
      {now.toISOString().slice(11, 19)} <span className="hom-muted">UTC</span>
    </span>
  )
}

function TopBar({ controller, settings, registry, onBrief, onRecon, recon }) {
  const enabled = LAYERS.filter(l => settings.layers[l.id])
  const live = enabled.filter(l => controller.layerState(l.id).status === 'ok').length
  const failing = enabled.filter(l => controller.layerState(l.id).status === 'error').length
  const tone = registry.state === 'missing' || registry.state === 'error' ? 'bad' : failing ? 'warn' : live ? 'live' : 'idle'
  const label = registry.state === 'missing' ? 'Backend off' : registry.state === 'error' ? 'Backend error' : `${live}/${enabled.length} live`
  return (
    <header className="hom-top">
      <div className="hom-brand">
        <div className="hom-brand-row">
          <span className="hom-title">OSINT Map</span>
          <span className={`hom-status hom-tone-${tone}`} role="status" aria-live="polite">
            <span className="hom-dot" data-pulse={tone === 'live'} />
            <b>{label}</b>
          </span>
          <UtcClock />
        </div>
        <span className="hom-subtitle">Open-source situational awareness: hazards, traffic, orbits and incidents, live.</span>
      </div>
      <div className="hom-top-actions">
        <Tip label="Open the recon toolkit (/)">
          <button type="button" className="hom-btn hom-btn--ghost" aria-pressed={recon} onClick={onRecon}>
            <Codicon name="search" /> Recon
          </button>
        </Tip>
        <Tip label="Hermes writes a situational brief from the live layers">
          <button type="button" className="hom-btn hom-btn--primary" onClick={onBrief}>
            <Codicon name="sparkle" /> Brief me
          </button>
        </Tip>
      </div>
    </header>
  )
}

function Dock({ controller, settings, narrow, openRail, setOpenRail, recon, setRecon, onReset }) {
  return (
    <nav className="hom-dock hom-panel" aria-label="Map controls">
      {narrow ? (
        <>
          <Tip label="Layers and panels">
            <button type="button" className="hom-btn" aria-pressed={openRail === 'left'} onClick={() => setOpenRail(openRail === 'left' ? null : 'left')} aria-label="Layers and panels">
              <Codicon name="layers" />
            </button>
          </Tip>
          <Tip label="Feed and details">
            <button type="button" className="hom-btn" aria-pressed={openRail === 'right'} onClick={() => setOpenRail(openRail === 'right' ? null : 'right')} aria-label="Feed">
              <Codicon name="list-unordered" />
            </button>
          </Tip>
          <span className="hom-sep" />
        </>
      ) : null}
      <Tip label="Spin the globe when idle (A)">
        <button type="button" className="hom-btn" aria-pressed={settings.autoRotate} onClick={() => controller.setAutoRotate(!settings.autoRotate)} aria-label="Auto-rotate">
          <Codicon name="sync" />
        </button>
      </Tip>
      <Tip label="Reset view (R)">
        <button type="button" className="hom-btn" onClick={onReset} aria-label="Reset view">
          <Codicon name="screen-normal" />
        </button>
      </Tip>
      <Tip label="Dithered or smooth globe (D)">
        <button type="button" className="hom-btn" aria-pressed={settings.dither} onClick={() => controller.setDither(!settings.dither)} aria-label="Dithered globe">
          <Codicon name="symbol-color" />
        </button>
      </Tip>
      <Tip label="Refresh every layer now">
        <button type="button" className="hom-btn" onClick={() => controller.refresh()} aria-label="Refresh">
          <Codicon name="refresh" />
        </button>
      </Tip>
      <span className="hom-sep" />
      <Tip label="Recon toolkit (/)">
        <button type="button" className="hom-btn" aria-pressed={recon} onClick={() => setRecon(!recon)} aria-label="Recon">
          <Codicon name="search" />
          {narrow ? null : <span>Recon</span>}
        </button>
      </Tip>
    </nav>
  )
}

