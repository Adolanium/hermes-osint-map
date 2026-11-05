// The camera wall: when you open a city's camera cluster, its nearest cameras as a live grid.
// Each tile is the same /camera/{id} still the camera panel shows, refreshed every minute.
import { useEffect, useState } from 'react'
import { Codicon } from '@hermes/plugin-sdk'
import { useRemote } from './panels.jsx'
import { age, useNow } from './kit.jsx'

export const WALL_SIZE = 9
const POOL_SIZE = 30 // candidates, so cameras that are down can be swapped for the next nearest

/** True for an operator's "camera unavailable" card: an image that is mostly flat white.
 *  (Caltrans serves one per camera, each slightly different, so it can't be matched by bytes.) */
export function looksUnavailable(src) {
  return new Promise(resolve => {
    const img = new Image()
    img.onload = () => {
      try {
        const c = document.createElement('canvas')
        c.width = 48
        c.height = 27
        const g = c.getContext('2d', { willReadFrequently: true })
        g.drawImage(img, 0, 0, 48, 27)
        const px = g.getImageData(0, 0, 48, 27).data
        let white = 0
        for (let i = 0; i < px.length; i += 4) if (px[i] > 228 && px[i + 1] > 228 && px[i + 2] > 228) white++
        resolve(white / (px.length / 4) > 0.55)
      } catch {
        resolve(false)
      }
    }
    img.onerror = () => resolve(false)
    img.src = src
  })
}

/** The cluster's cameras nearest its centre, so the wall shows one neighbourhood. */
export function wallCameras(items, center, n = POOL_SIZE) {
  const cos = Math.cos((center.lat * Math.PI) / 180)
  return items
    .filter(i => i.layer === 'cameras' && i.lat !== null)
    .map(i => ({ item: i, d: (i.lat - center.lat) ** 2 + (((((i.lon - center.lon + 540) % 360) - 180) * cos) ** 2) }))
    .sort((a, b) => a.d - b.d)
    .slice(0, n)
    .map(x => x.item)
}

function Tile({ rest, item, now, onOpen, index, onDown }) {
  const { data, error, loading } = useRemote(rest, `/camera/${encodeURIComponent(item.id)}`, 60_000)
  useEffect(() => {
    if (error && !data) onDown(item.id)
    else if (data?.image) looksUnavailable(data.image).then(down => down && onDown(item.id))
  }, [data?.image, error]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <button type="button" className="hom-wall-tile" onClick={() => onOpen(item)} style={{ animationDelay: `${index * 45}ms` }} title={item.title}>
      {data?.image ? <img src={data.image} alt={`Latest image from ${item.title}`} /> : <div className={loading ? 'hom-skeleton' : 'hom-camera-empty'}>{loading ? null : error ? 'No image' : ''}</div>}
      <span className="hom-wall-cap">
        <span className="hom-dot hom-tone-live" data-pulse={!!data?.image} />
        <span className="hom-wall-name">{item.title}</span>
        {data?.captured ? <span className="hom-num">{age(data.captured, now)}</span> : null}
      </span>
    </button>
  )
}

export function CameraWall({ rest, wall, onClose, onOpen }) {
  const now = useNow(15_000)
  const [down, setDown] = useState(() => new Set())
  const shown = wall.items.filter(i => !down.has(i.id)).slice(0, WALL_SIZE)
  const markDown = id => setDown(prev => (prev.has(id) ? prev : new Set(prev).add(id)))
  return (
    <section className="hom-panel hom-wall hom-rise" aria-label={`Cameras near ${wall.label}`}>
      <div className="hom-card-head">
        <span className="hom-label">
          <Codicon name="device-camera-video" /> {wall.label}
        </span>
        <span className="hom-muted hom-num" style={{ fontSize: 10.5 }}>
          {shown.length} of {wall.count} · live stills
        </span>
        <button type="button" className="hom-btn" onClick={onClose} aria-label="Close the camera wall">
          <Codicon name="close" />
        </button>
      </div>
      <div className="hom-wall-grid">
        {shown.map((item, i) => (
          <Tile key={item.id} rest={rest} item={item} now={now} onOpen={onOpen} index={i} onDown={markDown} />
        ))}
      </div>
      <div className="hom-caption">{wall.operator} · official open data · click a camera for its details</div>
    </section>
  )
}
