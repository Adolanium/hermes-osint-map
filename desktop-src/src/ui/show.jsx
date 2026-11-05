// Scenes from Hermes: the osint_show tool stores one in the plugin's state, this page polls /show,
// flies the globe there and draws Hermes's pins and ring on top, attached to the globe as it moves.
import { useEffect, useRef, useState } from 'react'
import { Codicon } from '@hermes/plugin-sdk'

const POLL_MS = 1500
const FRESH_MS = 2 * 60_000 // a scene this new is still shown when the page opens
const EARTH_KM = 6371

/** Poll /show; call onScene(scene) for each new one. */
export function useShowFeed(rest, onScene) {
  const onSceneRef = useRef(onScene)
  onSceneRef.current = onScene
  useEffect(() => {
    let cursor = null
    let stopped = false
    let timer = 0
    const tick = async () => {
      try {
        const answer = await rest(`/show?after=${cursor ?? 0}`)
        if (stopped || !answer?.ok) return
        const first = cursor === null
        if (answer.scene && (!first || (answer.at && Date.now() - answer.at < FRESH_MS))) onSceneRef.current({ ...answer.scene, seq: answer.seq })
        cursor = answer.seq ?? cursor ?? 0
      } catch {
        // the route mounts on first use; the next tick retries
      } finally {
        if (!stopped) timer = setTimeout(tick, document.hidden ? POLL_MS * 4 : POLL_MS)
      }
    }
    tick()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [rest])
}

/** Points on the small circle `radiusKm` around a centre, for drawing a ring on the globe. */
export function ringPoints(lat0, lon0, radiusKm, n = 96) {
  const d = radiusKm / EARTH_KM
  const p0 = (lat0 * Math.PI) / 180
  const l0 = (lon0 * Math.PI) / 180
  const out = []
  for (let i = 0; i <= n; i++) {
    const t = (i / n) * 2 * Math.PI
    const lat = Math.asin(Math.sin(p0) * Math.cos(d) + Math.cos(p0) * Math.sin(d) * Math.cos(t))
    const lon = l0 + Math.atan2(Math.sin(t) * Math.sin(d) * Math.cos(p0), Math.cos(d) - Math.sin(p0) * Math.sin(lat))
    out.push({ lat: (lat * 180) / Math.PI, lon: ((((lon * 180) / Math.PI + 540) % 360) - 180) })
  }
  return out
}

/** Where to look for a scene: its focus, or the middle of its pins and ring. */
export function sceneTarget(scene) {
  if (scene.focus && scene.focus.lat !== undefined) return { lat: scene.focus.lat, lon: scene.focus.lon, radiusKm: scene.focus.radius_km }
  const pts = [...(scene.pins ?? []), ...(scene.ring ? [scene.ring] : [])]
  if (!pts.length) return null
  const lat = pts.reduce((s, p) => s + p.lat, 0) / pts.length
  const lon = pts.reduce((s, p) => s + p.lon, 0) / pts.length
  const spread = Math.max(...pts.map(p => Math.hypot(p.lat - lat, (p.lon - lon) * Math.cos((lat * Math.PI) / 180)) * 111), scene.ring?.radius_km ?? 0)
  return { lat, lon, radiusKm: Math.max(300, spread * 1.6) }
}

export function ShowOverlay({ globeRef, scene, onClose }) {
  const pinRefs = useRef([])
  const ringRef = useRef(null)
  const ringLabelRef = useRef(null)
  const [born] = useState(() => Date.now())

  useEffect(() => {
    let id = 0
    const ring = scene.ring ? ringPoints(scene.ring.lat, scene.ring.lon, scene.ring.radius_km) : null
    const frame = () => {
      const g = globeRef.current
      if (g) {
        scene.pins.forEach((pin, i) => {
          const el = pinRefs.current[i]
          if (!el) return
          const at = g.screenOf(pin.lat, pin.lon)
          el.style.opacity = at ? '1' : '0'
          if (at) el.style.transform = `translate(${at.x}px, ${at.y}px)`
        })
        if (ring && ringRef.current) {
          let d = ''
          let pen = false
          let top = null
          for (const p of ring) {
            const at = g.screenOf(p.lat, p.lon)
            if (!at) {
              pen = false
              continue
            }
            d += `${pen ? 'L' : 'M'}${at.x.toFixed(1)} ${at.y.toFixed(1)}`
            pen = true
            if (!top || at.y < top.y) top = at
          }
          ringRef.current.setAttribute('d', d)
          if (ringLabelRef.current) {
            ringLabelRef.current.style.opacity = top ? '1' : '0'
            if (top) ringLabelRef.current.style.transform = `translate(${top.x}px, ${top.y}px)`
          }
        }
      }
      id = requestAnimationFrame(frame)
    }
    id = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(id)
  }, [scene, globeRef])

  return (
    <>
      <div className="hom-show" aria-hidden="true">
        {scene.ring ? (
          <svg className="hom-show-ring">
            <path ref={ringRef} />
          </svg>
        ) : null}
        {scene.ring?.label ? (
          <span ref={ringLabelRef} className="hom-show-ring-label hom-mono">
            {scene.ring.label}
          </span>
        ) : null}
        {scene.pins.map((pin, i) => (
          <span key={`${scene.seq}:${i}`} ref={el => (pinRefs.current[i] = el)} className="hom-show-pin" style={{ opacity: 0 }}>
            <span className="hom-show-dot" style={{ animationDelay: `${(i % 6) * 120}ms` }} />
            <span className="hom-show-tag" style={{ animationDelay: `${200 + i * 90}ms` }}>
              {pin.label}
            </span>
          </span>
        ))}
      </div>
      <div key={scene.seq} className="hom-show-caption hom-panel" role="status" aria-live="polite">
        <span className="hom-show-by hom-mono">
          <Codicon name="sparkle" /> Hermes
        </span>
        <span className="hom-show-text">{scene.caption}</span>
        <span className="hom-muted hom-num">{new Date(born).toISOString().slice(11, 16)} UTC</span>
        <button type="button" className="hom-btn" onClick={onClose} aria-label="Clear what Hermes put on the map">
          <Codicon name="close" />
        </button>
      </div>
    </>
  )
}
