// The big camera view: one camera's latest still filling the globe area, refreshed every minute.
// Left and right arrows step to the next nearest camera, Escape or a click outside closes it.
import { useEffect, useRef, useState } from 'react'
import { Codicon } from '@hermes/plugin-sdk'
import { useRemote } from './panels.jsx'
import { age, useNow } from './kit.jsx'
import { looksUnavailable } from './wall.jsx'
import { CLIP_REFRESH_MS, ClipVideo, useCameraClip } from './clip.jsx'

export { nearestCameras } from '../data/cameras.js'

const REFRESH_MS = 60_000

export function CameraViewer({ rest, viewer, setViewer, onDetails }) {
  const item = viewer.items[viewer.index]
  const remote = useRemote(rest, item ? `/camera/${encodeURIComponent(item.id)}` : null, REFRESH_MS)
  // useRemote keeps the last answer while the next camera loads: only show a still that is this camera's.
  const data = remote.data?.id === item?.id ? remote.data : null
  const { error } = remote
  const loading = remote.loading || (!data && !error)
  const clip = useCameraClip(rest, item)
  const refreshMs = clip.src ? CLIP_REFRESH_MS : REFRESH_MS
  const now = useNow(1000)
  const dialogRef = useRef(null)
  // The still is re-requested every REFRESH_MS from when this camera was opened (useRemote's interval).
  const [openedAt, setOpenedAt] = useState(() => Date.now())
  useEffect(() => setOpenedAt(Date.now()), [item?.id])
  const dir = useRef(1)
  const step = d => {
    dir.current = d
    setViewer(v => ({ ...v, index: (v.index + d + v.items.length) % v.items.length }))
  }
  // Cameras that are off serve an "unavailable" card: skip past them in the direction you were going.
  const skipped = useRef(0)
  useEffect(() => {
    if (!data?.image || viewer.items.length < 2) return
    let live = true
    looksUnavailable(data.image).then(down => {
      if (!live || !down || skipped.current >= viewer.items.length - 1) return
      skipped.current += 1
      step(dir.current)
    })
    return () => {
      live = false
    }
  }, [data?.image]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => dialogRef.current?.focus(), [])
  if (!item) return null
  const next = Math.min(refreshMs / 1000, Math.max(1, Math.ceil((refreshMs - ((now - openedAt) % refreshMs)) / 1000)))
  const captured = (clip.src ? clip.data?.captured : null) ?? data?.captured
  const [operator, ...rest2] = (item.subtitle ?? '').split(' · ')

  return (
    <div
      className="hom-viewer"
      role="dialog"
      aria-modal="true"
      aria-label={`Camera: ${item.title}`}
      ref={dialogRef}
      tabIndex={-1}
      onClick={e => e.target === e.currentTarget && setViewer(null)}
      onKeyDown={e => {
        if (e.key === 'Escape') setViewer(null)
        else if (e.key === 'ArrowRight') step(1)
        else if (e.key === 'ArrowLeft') step(-1)
        else return
        e.preventDefault()
        e.stopPropagation()
      }}
    >
      <figure className="hom-viewer-frame hom-panel">
        <div className="hom-viewer-img">
          {clip.src ? (
            <ClipVideo key={clip.src} src={clip.src} title={item.title} fit="contain" />
          ) : data?.image ? (
            <img key={data.image.length + item.id} src={data.image} alt={`Latest image from ${item.title}`} />
          ) : (
            <div className={loading ? 'hom-skeleton' : 'hom-camera-empty'}>{loading ? null : error ? `No image right now: ${error}` : 'No image'}</div>
          )}
          <span className="hom-viewer-live hom-mono">
            <span className="hom-dot hom-tone-live" data-pulse={!!(clip.src || data?.image)} /> {clip.src ? 'LIVE VIDEO' : 'LIVE STILL'}
            {viewer.items.length > 1 ? ` · ${viewer.index + 1} / ${viewer.items.length}` : ''}
          </span>
          {viewer.items.length > 1 ? (
            <>
              <button type="button" className="hom-viewer-nav" data-side="left" onClick={() => step(-1)} aria-label="Previous camera (←)">
                <Codicon name="chevron-left" />
              </button>
              <button type="button" className="hom-viewer-nav" data-side="right" onClick={() => step(1)} aria-label="Next nearest camera (→)">
                <Codicon name="chevron-right" />
              </button>
            </>
          ) : null}
        </div>
        <figcaption className="hom-viewer-cap">
          <div className="hom-viewer-title">
            <span>{item.title}</span>
            <span className="hom-soft">{[operator, ...rest2].filter(Boolean).join(' · ')}</span>
          </div>
          <div className="hom-viewer-meta hom-num">
            {captured ? <span>{clip.src ? 'Clip captured' : 'Captured'} {age(captured, now)} ago</span> : null}
            <span>{clip.src ? 'Next clip' : 'Next still'} in {next}s</span>
          </div>
          <div className="hom-viewer-actions">
            <button type="button" className="hom-btn hom-btn--ghost" onClick={() => onDetails(item)}>
              <Codicon name="location" /> Show on map
            </button>
            <button type="button" className="hom-btn" onClick={() => setViewer(null)} aria-label="Close (Esc)">
              <Codicon name="close" />
            </button>
          </div>
          {data?.attribution ? <div className="hom-caption hom-viewer-attr">{data.attribution}</div> : null}
        </figcaption>
      </figure>
    </div>
  )
}
