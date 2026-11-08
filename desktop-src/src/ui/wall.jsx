// The camera wall: when you open a city's camera cluster, its nearest cameras as a live grid.
// Each tile is the same /camera/{id} still the camera panel shows, refreshed every minute.
import { useEffect, useState } from 'react'
import { Codicon } from '@hermes/plugin-sdk'
import { useRemote } from './panels.jsx'
import { age, useNow } from './kit.jsx'
import { ClipVideo, useCameraClip } from './clip.jsx'
import { wallCameras } from '../data/cameras.js'

export const WALL_SIZE = 9
export { wallCameras } // page.jsx imports it from here; the helper lives with nearestCameras

/** True for an operator's "camera unavailable" card: an image that is mostly one flat colour
 *  (Caltrans's white card, TfL's grey "camera in use" card). Each one differs slightly, so they
 *  can't be matched by bytes. Dark pixels don't count, so night scenes are kept. */
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
        const buckets = new Map()
        for (let i = 0; i < px.length; i += 4) {
          if (px[i] + px[i + 1] + px[i + 2] < 120) continue
          const key = ((px[i] >> 5) << 6) | ((px[i + 1] >> 5) << 3) | (px[i + 2] >> 5)
          buckets.set(key, (buckets.get(key) ?? 0) + 1)
        }
        const top = Math.max(0, ...buckets.values())
        // Cards score 0.72+ (measured on Caltrans and TfL); busy grey road scenes reach about 0.55.
        resolve(top / (px.length / 4) > 0.68)
      } catch {
        resolve(false)
      }
    }
    img.onerror = () => resolve(false)
    img.src = src
  })
}

function Tile({ rest, item, now, onOpen, index, onDown }) {
  const { data, error, loading } = useRemote(rest, `/camera/${encodeURIComponent(item.id)}`, 60_000)
  const clip = useCameraClip(rest, item)
  useEffect(() => {
    if (error && !data) onDown(item.id)
    else if (data?.image) looksUnavailable(data.image).then(down => down && onDown(item.id))
  }, [data?.image, error]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <button type="button" className="hom-wall-tile" onClick={() => onOpen(item)} style={{ animationDelay: `${index * 45}ms` }} title={item.title}>
      {clip.src ? <ClipVideo src={clip.src} title={item.title} /> : data?.image ? <img src={data.image} alt={`Latest image from ${item.title}`} /> : <div className={loading ? 'hom-skeleton' : 'hom-camera-empty'}>{loading ? null : error ? 'No image' : ''}</div>}
      <span className="hom-wall-cap">
        <span className="hom-dot hom-tone-live" data-pulse={!!data?.image} />
        <span className="hom-wall-name">{item.title}</span>
        {clip.src ? <span className="hom-mono">VIDEO</span> : data?.captured ? <span className="hom-num">{age(data.captured, now)}</span> : null}
      </span>
    </button>
  )
}

export function CameraWall({ rest, wall, onClose, onOpen }) {
  const now = useNow(15_000)
  const [large, setLarge] = useState(false)
  const [down, setDown] = useState(() => new Set())
  const shown = wall.items.filter(i => !down.has(i.id)).slice(0, WALL_SIZE)
  const markDown = id => setDown(prev => (prev.has(id) ? prev : new Set(prev).add(id)))
  return (
    <section className="hom-panel hom-wall hom-rise" data-large={large} aria-label={`Cameras near ${wall.label}`}>
      <div className="hom-card-head">
        <span className="hom-label">
          <Codicon name="device-camera-video" /> {wall.label}
        </span>
        <span className="hom-muted hom-num" style={{ fontSize: 10.5 }}>
          {shown.length} of {wall.count} · {shown.some(i => i.video) ? 'live video' : 'live stills'}
        </span>
        <button type="button" className="hom-btn" aria-pressed={large} onClick={() => setLarge(v => !v)} aria-label={large ? 'Smaller wall' : 'Full screen wall'} title={large ? 'Smaller wall' : 'Full screen wall'}>
          <Codicon name={large ? 'screen-normal' : 'screen-full'} />
        </button>
        <button type="button" className="hom-btn" onClick={onClose} aria-label="Close the camera wall">
          <Codicon name="close" />
        </button>
      </div>
      <div className="hom-wall-grid">
        {shown.map((item, i) => (
          <Tile key={item.id} rest={rest} item={item} now={now} onOpen={onOpen} index={i} onDown={markDown} />
        ))}
        {shown.length ? null : (
          <div className="hom-camera-empty" style={{ gridColumn: '1 / -1', padding: '28px 8px' }}>
            No images from this operator right now
          </div>
        )}
      </div>
      <div className="hom-caption">{wall.operator} · official open data · click a camera to open it large</div>
    </section>
  )
}
