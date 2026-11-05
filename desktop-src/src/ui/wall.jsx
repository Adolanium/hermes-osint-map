// The camera wall: when you open a city's camera cluster, its nearest cameras as a live grid.
// Each tile is the same /camera/{id} still the camera panel shows, refreshed every minute.
import { Codicon } from '@hermes/plugin-sdk'
import { useRemote } from './panels.jsx'
import { age, useNow } from './kit.jsx'

export const WALL_SIZE = 9

/** The cluster's cameras nearest its centre, so the wall shows one neighbourhood. */
export function wallCameras(items, center, n = WALL_SIZE) {
  const cos = Math.cos((center.lat * Math.PI) / 180)
  return items
    .filter(i => i.layer === 'cameras' && i.lat !== null)
    .map(i => ({ item: i, d: (i.lat - center.lat) ** 2 + (((((i.lon - center.lon + 540) % 360) - 180) * cos) ** 2) }))
    .sort((a, b) => a.d - b.d)
    .slice(0, n)
    .map(x => x.item)
}

function Tile({ rest, item, now, onOpen, index }) {
  const { data, error, loading } = useRemote(rest, `/camera/${encodeURIComponent(item.id)}`, 60_000)
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
  return (
    <section className="hom-panel hom-wall hom-rise" aria-label={`Cameras near ${wall.label}`}>
      <div className="hom-card-head">
        <span className="hom-label">
          <Codicon name="device-camera-video" /> {wall.label}
        </span>
        <span className="hom-muted hom-num" style={{ fontSize: 10.5 }}>
          {wall.items.length} of {wall.count} · live stills
        </span>
        <button type="button" className="hom-btn" onClick={onClose} aria-label="Close the camera wall">
          <Codicon name="close" />
        </button>
      </div>
      <div className="hom-wall-grid">
        {wall.items.map((item, i) => (
          <Tile key={item.id} rest={rest} item={item} now={now} onOpen={onOpen} index={i} />
        ))}
      </div>
      <div className="hom-caption">{wall.operator} · official open data · click a camera for its details</div>
    </section>
  )
}
