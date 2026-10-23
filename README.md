# Hermes OSINT Map

A live situational-awareness page for Hermes Desktop. Earthquakes, disaster alerts, natural events,
severe weather, aircraft, satellites, news-derived incidents and shipping chokepoints share one
dithered 3D globe, with headlines, space weather, markets, country dossiers and a passive recon
toolkit. Hermes itself writes the briefings.

![OSINT Map in Hermes Desktop](assets/screenshot-overview.webp)

Unofficial community plugin; not affiliated with any of the data providers below.
**Every source is public and keyless.**

## What you get

- **A globe you can explore.** Drag to spin (with inertia), scroll or pinch to zoom, double-click to
  dive in. It spins slowly when idle. Countries with the most incidents and disasters glow.
- **Eight live layers that read without colour.** Each has its own shape and theme colour; older
  events fade into the background and new ones pulse once as they arrive:

  | Layer | Shape | Source | Refresh |
  |---|---|---|---|
  | Earthquakes | ● circle | USGS (M2.5+ last day, M4.5+ last week) | 1 min |
  | Disaster alerts | ◆ diamond | GDACS | 10 min |
  | Natural events | ▲ triangle | NASA EONET (last 30 days) | 10 min |
  | Severe weather (US) | ■ square | National Weather Service | 5 min |
  | News incidents | ⬢ hexagon | GDELT events, last 3 hours | 15 min |
  | Aircraft | ➤ dart | adsb.lol: military worldwide, all traffic near the view when zoomed in | 15 s |
  | Satellites | ✚ plus | CelesTrak elements, positions computed live (SGP4) | 1 s |
  | Chokepoints | ○ ring | Curated straits and canals | static |

- **Moving things you can follow.** Click a satellite to draw its next orbit, or an aircraft to see
  the track it has flown while you watched.
- **Country dossiers.** Click a country: World Bank facts, everything on the map there, and recent
  headlines.
- **Panels.** Layer health and freshness, NOAA space weather (Kp and the R/S/G scales), markets, and a
  feed with events (filterable by layer), world headlines and alerts for significant earthquakes and
  orange or red GDACS alerts.
- **Passive recon.** DNS, RDAP/WHOIS, TLS certificates, HTTP security headers, certificate-transparency
  subdomains, CVEs with CISA known-exploited status, IP geolocation, GitHub profiles and MAC vendors.
  Lookups that connect to a host refuse private, loopback and other internal addresses.
- **Hermes as the analyst.** "Brief me", "Ask Hermes" on any item, "Ask Hermes for a dossier" and "Ask
  Hermes to investigate" open a chat with a structured request. Hermes answers with the plugin's
  tools, which also work from any chat or messaging platform:

  | Tool | Answers |
  |---|---|
  | `osint_situation` | what's happening right now across every layer, plus Kp and top headlines |
  | `osint_events` | events by layer, country, or a point and radius |
  | `osint_country` | a country's facts, current events and headlines |
  | `osint_recon` | any recon lookup |
  | `osint_news` | headline search |

| Satellite tracking | Country dossier |
|---|---|
| ![The ISS with its orbit](assets/screenshot-satellite.webp) | ![Japan's dossier with nearby traffic](assets/screenshot-dossier.webp) |

| Aircraft | Recon |
|---|---|
| ![A flight over Europe](assets/screenshot-aircraft.webp) | ![A CVE lookup](assets/screenshot-recon.webp) |

It follows your Hermes theme, respects reduced motion, works in split tiles and narrow panes, and
stops all polling and rendering when you leave the page or the window is hidden. Keys: `1`–`8`
toggle layers, `/` opens recon, `R` resets the view, `A` toggles spin, `D` toggles dithering,
arrows rotate, `+`/`-` zoom, `Esc` closes a panel.

## Install

Requires Hermes Agent 0.22.2 or newer with Hermes Desktop.

1. Put this folder in your Hermes plugins directory, e.g. clone it:

   ```bash
   git clone https://github.com/Adolanium/hermes-osint-map ~/.hermes/plugins/hermes-osint-map
   ```

   (Windows: `%LOCALAPPDATA%\hermes\plugins\hermes-osint-map`.) Or use **Capabilities → Plugins →
   Install from Git** in Hermes Desktop.
2. In **Capabilities → Plugins → OSINT Map**, turn on **Agent in Hermes** (the backend and tools) and
   **Desktop** (the page). If the page says its backend isn't loaded, restart the gateway.
3. Open **OSINT Map** from the sidebar or the ⌘K/Ctrl+K palette ("Open OSINT Map").
4. Optional: add a free [Twelve Data](https://twelvedata.com) API key in the plugin's settings to add
   defense stocks, energy, metals and the S&P 500 to the markets panel. Without it the panel shows
   crypto.

## Data sources

Everything comes from public endpoints that need no account. The backend caches each source, so any
number of windows and agent calls cost one upstream request, and it identifies itself honestly
(`hermes-osint-map`). Please respect each provider's terms:

| Data | Provider | Terms |
|---|---|---|
| Earthquakes | [USGS](https://earthquake.usgs.gov) | public domain |
| Natural events | [NASA EONET](https://eonet.gsfc.nasa.gov) | public domain |
| Disaster alerts | [GDACS](https://www.gdacs.org) | free, with attribution |
| Severe weather | [NWS](https://www.weather.gov) | public domain |
| Incidents | [GDELT](https://www.gdeltproject.org) | free, with attribution |
| Aircraft | [adsb.lol](https://adsb.lol) | ODbL |
| Satellites | [CelesTrak](https://celestrak.org) | free, with attribution |
| Space weather | [NOAA SWPC](https://www.swpc.noaa.gov) | public domain |
| Country facts | [World Bank](https://data.worldbank.org) | CC BY 4.0 |
| Headlines | BBC, Al Jazeera, DW, The Guardian, France 24 RSS | headlines and links only |
| Crypto | [CoinGecko](https://www.coingecko.com) | free tier |
| Recon | Google Public DNS, rdap.org, crt.sh, CIRCL CVE, CISA KEV, ipwho.is, GitHub, MacVendors | public APIs |

News incidents are machine-coded from news articles. They show where coverage is happening, not
verified events, and the page and tools say so. Recon lookups are passive: they read public records
and one connection to the host you name; nothing is scanned.

## How it works

```
public sources ──► osintmap/ (gateway)                ──(poll per layer)──►  desktop/plugin.js
                   one parser per source, TTL caches,                         three.js globe,
                   GDELT rolling window, SSRF guard                           React panels, SGP4
```

- **Backend** (`osintmap/`, `dashboard/plugin_api.py`): parsers are pure functions tested against
  recorded responses. A source that fails keeps serving its last good data, marked stale, and is
  left alone for a while before the next try. adsb.lol requests are spaced out and retried once after
  a rate limit. Memory is bounded everywhere.
- **Desktop** (`desktop-src/`, bundled to `desktop/plugin.js`): the globe draws every layer in four
  draw calls. Country borders are a baked Natural Earth raster sampled in the shader; markers are one
  GPU-instanced pool of up to 8,192 billboards with a shape per layer; satellites are propagated from
  orbital elements once a second. Each layer is polled at its own cadence, only while the page is
  open.

## Development

```bash
cd desktop-src
npm ci
npm run build    # writes ../desktop/plugin.js
npm test         # page-side data handling (node --test)
```

Python tests (parsers, caches, recon guard, routes, tools) and a live check of every route:

```bash
python -m pip install pytest httpx fastapi
python -m pytest
python scripts/smoke.py
```

To develop against a running Hermes, link the checkout into your plugins directory (a symlink, or a
directory junction on Windows). Desktop then hot-reloads `desktop/plugin.js` on every build.
Backend changes need a gateway restart.

## Credits

- Data: the providers listed above.
- Borders: [Natural Earth](https://www.naturalearthdata.com) via
  [world-atlas](https://github.com/topojson/world-atlas), public domain.
- Rendering: [three.js](https://threejs.org) (MIT); orbits:
  [satellite.js](https://github.com/shashwatak/satellite-js) (MIT).

MIT licensed. See [LICENSE](LICENSE).
