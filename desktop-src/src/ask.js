// Hand work to the Hermes agent: open a fresh chat and send a prepared request. The agent answers
// with this plugin's osint_* tools (and anything else it has), so briefings and dossiers are written
// by Hermes itself rather than by a model wired into the page.
import { LAYER_INDEX, LAYERS } from './data/layers.js'
import { countryName } from './geo/world.js'

const fmtTime = t => (t ? new Date(t).toISOString().replace('T', ' ').slice(0, 16) + ' UTC' : 'time unknown')

/** Open a new chat and send `prompt`. Resolves true once the composer accepted it. */
export async function askHermes(host, prompt, { notify } = {}) {
  try {
    host.newChat()
  } catch {
    // fall through: try the active composer
  }
  const deadline = Date.now() + 5000
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 180))
    try {
      if (host.composer?.submit('new', prompt)) return true
    } catch {
      // composer not mounted yet
    }
  }
  // Last resort: leave the request in the draft for the user to send.
  try {
    if (await host.composer?.setDraft('new', prompt)) {
      notify?.('The request is in a new chat; press Enter to send it.')
      return true
    }
  } catch {
    // ignore
  }
  notify?.('Could not open a chat for Hermes. Open one and try again.')
  return false
}

export const prompts = {
  brief: () =>
    [
      'Give me a situational brief of the world right now.',
      'Use the osint_map tools: start with osint_situation, then osint_events and osint_news where they add something.',
      'Structure: BLUF (2–3 sentences); key developments by region, each with a confidence level (high/medium/low) and its source and time;',
      'natural hazards (earthquakes, disaster alerts, severe weather, space weather); what to watch in the next 24–72 hours.',
      'Keep what the data shows separate from what you infer, and say when a source is thin.'
    ].join(' '),

  country: code =>
    [
      `Write a region dossier for ${countryName(code)} (${code}).`,
      `Use osint_country("${code}") first, then osint_events and osint_news for context; add web research if you have it.`,
      'Structure: BLUF; current situation; active hazards and incidents (with sources and times); risk assessment with confidence levels;',
      'a watchlist of indicators for the next week; key facts. Mark anything inferred as such.'
    ].join(' '),

  item: item => {
    const layer = LAYERS[LAYER_INDEX[item.layer]]
    const facts = item.props.map(([k, v]) => `${k}: ${v}`).join('; ')
    const where = item.lat !== null ? `${item.lat.toFixed(2)}, ${item.lon.toFixed(2)}` : 'position unknown'
    return [
      `Tell me about this ${layer.label.toLowerCase()} entry from my OSINT map: "${item.title}" at ${where}, ${fmtTime(item.time)}.`,
      facts ? `Facts from the feed: ${facts}.` : '',
      item.url ? `Source: ${item.url}.` : '',
      'Use the osint_map tools (osint_events near that point, osint_news) and web search if available.',
      'Explain what it is, why it matters, and what is confirmed versus uncertain.'
    ]
      .filter(Boolean)
      .join(' ')
  },

  recon: (kind, target, summary) =>
    [
      `Investigate ${target} using passive OSINT only.`,
      summary?.length ? `My ${kind} lookup found: ${summary.map(([k, v]) => `${k}: ${v}`).join('; ')}.` : '',
      'Use osint_recon for the other relevant lookups (dns, rdap, tls, headers, subdomains, ip, cve as they apply).',
      'Summarise ownership, infrastructure, notable exposures or misconfigurations, and sensible next steps.',
      'Do not scan, brute-force or probe anything beyond these passive lookups.'
    ]
      .filter(Boolean)
      .join(' ')
}
