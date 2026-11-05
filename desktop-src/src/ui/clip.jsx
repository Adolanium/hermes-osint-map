// A camera's live video clip (TfL JamCams publish about ten seconds of real footage, re-shot every
// few minutes). The backend sends the MP4 as a data URL; it plays here from a blob URL, muted and
// looping, and the clip is fetched again every two minutes. Cameras without a clip show a still.
import { useEffect, useState } from 'react'
import { useRemote } from './panels.jsx'

export const CLIP_REFRESH_MS = 120_000

function toBlobUrl(dataUrl) {
  const comma = dataUrl.indexOf(',')
  const bytes = atob(dataUrl.slice(comma + 1))
  const buf = new Uint8Array(bytes.length)
  for (let i = 0; i < bytes.length; i++) buf[i] = bytes.charCodeAt(i)
  return URL.createObjectURL(new Blob([buf], { type: 'video/mp4' }))
}

/** `{ src, data, error, loading }` for a camera's clip; src is a blob URL, or null. */
export function useCameraClip(rest, item, enabled = true) {
  const on = enabled && !!item?.video
  const remote = useRemote(rest, on ? `/camera/${encodeURIComponent(item.id)}/clip` : null, CLIP_REFRESH_MS, on)
  const data = remote.data?.id === item?.id ? remote.data : null
  const [src, setSrc] = useState(null)
  useEffect(() => {
    if (!data?.video) {
      setSrc(null)
      return undefined
    }
    let url = null
    try {
      url = toBlobUrl(data.video)
    } catch {
      url = null
    }
    setSrc(url)
    return () => url && URL.revokeObjectURL(url)
  }, [data?.video])
  return { src, data, error: remote.error, loading: on && !data && !remote.error }
}

/** The clip as a muted, looping video that fills its box. */
export function ClipVideo({ src, title, className, fit = 'cover' }) {
  return <video className={className} src={src} autoPlay muted loop playsInline aria-label={`Live video from ${title}`} style={{ objectFit: fit }} />
}
