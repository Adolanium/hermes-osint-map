# Backend ↔ page contract

The Desktop page (`desktop-src/`) talks to the Python backend (`dashboard/plugin_api.py`) through
`ctx.rest('/…')`, which maps to `GET /api/plugins/hermes-osint-map/…`. The backend runs in the
gateway's plugin host and answers buffered JSON (no streaming), so the page polls.

Every response is HTTP 200 JSON: `{"ok": true, ...}` or `{"ok": false, "error": "human sentence",
"status": <int>}`. Never raise out of a route.

All times are epoch milliseconds (int). All coordinates are WGS84 degrees. Strings shown to the user
are plain text (the page escapes nothing; never return HTML).

## Layers

`GET /layers` → registry and health, cheap (no upstream calls):

```json
{"ok": true, "layers": [
  {"id": "quakes", "label": "Earthquakes", "status": "ok|stale|error|idle|disabled",
   "error": null, "updated": 1792730540000, "count": 143, "refresh_s": 60,
   "source": "USGS", "source_url": "https://earthquake.usgs.gov", "attribution": "USGS (public domain)",
   "needs_key": null}
]}
```

`status`: `idle` = never fetched yet; `stale` = last refresh failed but older data is served;
`disabled` = needs an env key that is not set (`needs_key` names it).

`GET /layer/{id}` (+ optional query params per layer) → fetches through the layer's TTL cache:

```json
{"ok": true, "id": "quakes", "updated": 1792730540000, "status": "ok", "error": null,
 "items": [Item, ...]}
```

`Item`:

```json
{"id": "quakes:us7000abcd",          // stable across refreshes, prefixed with the layer id
 "layer": "quakes",
 "kind": "earthquake",               // sub-type, see per-layer table
 "lat": 35.1, "lon": 139.2,
 "alt_km": null,                     // only satellites/aircraft use it
 "title": "M 5.8 - 32 km SE of Hachijo, Japan",
 "subtitle": "Depth 10 km",          // optional, short
 "severity": 0.72,                   // 0..1, layer-normalised (drives marker size/brightness)
 "time": 1792730000000,              // event/observation time; null if not applicable
 "country": "JP",                    // ISO alpha-2 when the source gives it or it is derivable, else null
 "url": "https://…",                 // source page for "Open source", or null
 "heading": null, "speed_kts": null, // aircraft only
 "props": [["Magnitude", "5.8"], ["Depth", "10 km"]]   // display facts, ≤ 8 rows, strings
}
```

| id | label | source | kinds | refresh | notes |
|---|---|---|---|---|---|
| `quakes` | Earthquakes | USGS `summary/2.5_day.geojson` + `4.5_week.geojson` (dedupe by id) | `earthquake` | 60 s | severity from magnitude (M2.5→0.1, M7+→1) |
| `natural` | Natural events | NASA EONET v3 `events?status=open` | `wildfires`, `severeStorms`, `volcanoes`, `floods`, `seaLakeIce`, `dustHaze`, `landslides`, … (EONET category id) | 10 min | latest geometry point; polygons → centroid |
| `disasters` | Disaster alerts | GDACS `gdacsapi/api/events/geteventlist/SEARCH` (recent) | `EQ`,`TC`,`FL`,`VO`,`DR`,`WF` | 10 min | severity from alertlevel Green .3 / Orange .65 / Red 1 |
| `weather` | Severe weather (US) | NWS `api.weather.gov/alerts/active?severity=Extreme,Severe` | NWS `event` text | 5 min | polygon → centroid; zone-only alerts without geometry are dropped (counted) |
| `aircraft` | Aircraft | adsb.lol `v2/mil` (global military) + `v2/point/{lat}/{lon}/{radius}` when `?lat=&lon=` given (radius ≤ 250 nm) | `military`, `civil` | 15 s mil, 10 s point | `alt_km` from alt_baro ft; ground traffic dropped; title = callsign or reg; props: type, reg, squawk, alt, speed |
| `satellites` | Satellites | CelesTrak `gp.php?GROUP=…&FORMAT=json` for groups `stations`, `gps-ops`, `weather`, `military`? (only groups that exist; ≤ 400 objects total) | group name | 6 h | **items carry `omm` (the raw CelesTrak OMM dict) and lat/lon null**; the page propagates with SGP4 |
| `incidents` | News incidents | GDELT 2.0 events: `lastupdate.txt` → latest `export.CSV.zip`; keep a rolling 3 h window (12 files) | CAMEO root: `protest`, `force`, `assault`, `fight`, `mass-violence`, `coerce`, `threaten` | 15 min | only QuadClass 3/4 roots 14,17–20; aggregate same ActionGeo point+root into one item with `count`; title like "Fight · Kharkiv, Ukraine"; url = a SOURCEURL; severity from mentions + Goldstein |
| `chokepoints` | Chokepoints | static curated list in code (≈ 15: Hormuz, Bab-el-Mandeb, Suez, Malacca, Panama, Bosporus, Gibraltar, Dover, Taiwan Strait, Lombok, Danish straits, Cape of Good Hope, Kerch, Sunda, Magellan) | `strait`, `canal` | static | props: what passes through (plain facts, no numbers you can't source) |

Layer-specific query params are validated (floats in range, radius clamp) and are part of the cache key.

## Panels

`GET /news?q=&limit=` → `{"ok": true, "updated": ms, "items": [{"id", "title", "url", "source", "time", "summary"}]}`
Merged headlines from a few public RSS feeds (BBC World, Al Jazeera, DW, The Guardian World, France 24
English). `q` filters case-insensitively on title/summary. Cache 10 min. Plain text only (strip tags).

`GET /space-weather` → `{"ok": true, "updated": ms, "kp": [{"time", "kp"}] (last 7 days), "kp_now": 3.3,
"scales": {"R": 0, "S": 0, "G": 1} (from SWPC noaa-scales.json), "alerts": [{"time", "code", "headline"}] (latest 8)}`
Cache 5 min.

`GET /markets` → `{"ok": true, "updated": ms, "groups": [{"id": "crypto", "label": "Digital assets",
"items": [{"symbol", "label", "price", "change_pct", "currency"}]}], "notes": ["…"]}`
CoinGecko simple/price for BTC, ETH, SOL (no key). Equities/commodities (defense: LMT, RTX, NOC, GD, BA;
energy: CL (WTI), BZ (Brent), NG; materials: XAU, XAG, HG; indices: SPX) only when `TWELVEDATA_API_KEY`
is set, one batched quote call, cached 60 min; otherwise `notes` explains how to enable them.

`GET /country/{CC}` → World Bank (`api.worldbank.org/v2/country/{CC}?format=json` + indicators
SP.POP.TOTL, NY.GDP.MKTP.CD, NY.GDP.PCAP.CD latest non-null via `?mrnev=1`):
`{"ok": true, "code", "name", "capital", "region", "income", "lat", "lon", "population", "gdp_usd", "gdp_per_capita_usd", "year"}`. Cache 24 h.

## Recon

`GET /recon/{kind}?q=<target>` → `{"ok": true, "kind", "target", "summary": [["label", "value"], …],
"result": {...raw-ish but trimmed...}, "sources": ["…"]}`. Validate targets strictly; reject with
`ok: false` and a helpful sentence.

| kind | target | source |
|---|---|---|
| `dns` | domain | dns.google/resolve: A, AAAA, MX, NS, TXT, CNAME, CAA |
| `rdap` | domain or IP | rdap.org (follow redirects): registrar, created/expires, status, nameservers / network name, CIDR, org |
| `tls` | domain[:port] | direct TLS handshake (stdlib ssl, SNI, 8 s timeout): subject, issuer, SANs (≤ 50), validity, days left, protocol, cipher |
| `headers` | URL or domain | HTTPS GET with redirects (≤ 5), no body kept: status chain, server, security headers present/missing (HSTS, CSP, XFO, XCTO, Referrer-Policy, Permissions-Policy) |
| `subdomains` | domain | crt.sh `?q=%.domain&output=json` → unique names (≤ 300), first/last seen |
| `cve` | CVE-YYYY-NNNN | cve.circl.lu (CVE JSON 5) + CISA KEV catalog (cached 6 h): title, CVSS, CWE, published, KEV yes/no + due date |
| `ip` | IPv4/IPv6 | ipwho.is + RDAP ip: geo, ASN, org, network |
| `github` | user or org | api.github.com/users/{x} (+ public repos top 10 by stars) |
| `mac` | MAC/OUI | api.macvendors.com/{mac} (rate-limit aware) |

**SSRF guard (required):** `tls` and `headers` connect to user-supplied hosts from the gateway. Resolve
first and refuse loopback, private, link-local, multicast, reserved and CGNAT addresses (IPv4 and IPv6),
re-check after redirects, ports limited to 443/8443/80/8080 (80/8080 only for `headers`).
Nothing else may connect anywhere except the fixed upstream hosts above.

## Agent tools (Python, `provides_tools`)

Toolset `osint_map`. Plain-text results the model can quote, with source names.

- `osint_situation()` → counts and the most severe items per layer right now + Kp + top headlines.
- `osint_events(layer?, country?, lat?, lon?, radius_km?, limit?)` → items filtered by layer/country/point-radius.
- `osint_country(code)` → World Bank facts + events in that country across layers + matching headlines.
- `osint_recon(kind, target)` → same as the recon route, summarised.
- `osint_news(query, limit?)` → headline search.

## Politeness

One shared TTL cache per (route, params) in the process; refresh only when asked (the page polls
while open; tools call on demand). Honest User-Agent `hermes-osint-map/0.1 (+https://github.com/Adolanium/hermes-osint-map)`.
Timeouts 10–20 s. Bounded memory everywhere (cap items per layer at 5,000; aircraft at 3,000).

## Backend notes (as implemented, 2026-10-23)

Deviations from the text above:

- `incidents` keeps CAMEO roots **13, 14, 15, 17, 18, 19, 20** (QuadClass 3 or 4), not just 14 and 17–20, so
  every kind in the table can occur (`threaten` = 13, `force` = 15). Root 16 (reduce relations) is left out.
- `chokepoints` uses kind **`cape`** for the Cape of Good Hope (it is neither a strait nor a canal).

Additive fields (safe to ignore):

- `/layer/{id}` also returns `count`; `weather` adds `dropped` (zone-only alerts without geometry).
  `incidents` items carry `count` (reports folded into the item). Item ids are stable:
  `incidents:{root}:{lat},{lon}`, `aircraft:{icao hex}`, `satellites:{norad}`.
- A layer whose upstream is down with no cached data answers `ok: true, status: "error", items: []`.
  Bad query params and unknown layer ids answer `ok: false` (400 / 404).
- `/news` adds `status` (`ok`/`stale`), `error` and `feeds: [{source, status, error, count}]`; it answers
  `ok: false` only when no feed has ever answered. `limit` defaults to 50, max 200.
- `/space-weather` adds `status` and `error` (some SWPC products failed); `kp_now` falls back to the newest
  3-hour Kp when the 1-minute product is unavailable.
- `/markets`: crypto cached 5 min. Twelve Data quote symbols used: LMT, RTX, NOC, GD, BA, WTI/USD, XBR/USD,
  NG/USD, XAU/USD, XAG/USD, XCU/USD, SPX (shown as the contract's display symbols CL, BZ, NG, XAU, XAG, HG, SPX).
  The commodity symbols are untested without a key; any symbol Twelve Data refuses becomes a line in `notes`.
  The key is sent in an `Authorization: apikey …` header, never in the URL.
- `/country/{CC}` adds `years: {population, gdp_usd, gdp_per_capita_usd}`; `year` is the newest of them.

Recon `result` shapes (summary rows are the display contract; `result` is for detail views):

- `dns`: `{records: {A: [...], AAAA, MX, NS, TXT, CNAME, CAA}}`
- `rdap`: `{kind: "domain"|"ip", name, handle, status[], registered, expires, changed, registrar, registrant,
  nameservers[]}` (domain) or `{..., start, end, cidr[], country, type, org}` (ip)
- `tls`: `{host, port, address, trusted, verify_error, protocol, cipher, sha256, subject, issuer, sans[],
  san_count, serial, not_before, not_after, days_left}` (cert fields absent when the cert is not trusted)
- `headers`: `{chain: [{url, status, address, note}], server, powered_by, present: {label: value},
  missing: [label], headers: {name: value}}`
- `subdomains`: `{names: [{name, first_seen, last_seen, certs}], total}`. crt.sh is slow and often answers
  502; the wildcard query is retried once as a plain identity query before giving up.
- `cve`: `{id, state, title, description, cvss: {version, score, severity, vector}, cwe[], published,
  updated, assigner, kev: {added, due, ransomware, name, action} | null, kev_checked}`
- `ip`: `{geo: {country, country_code, region, city, lat, lon, asn, org, isp, domain, timezone}, network: rdap}`
- `github`: `{login, name, type, company, blog, location, bio, public_repos, followers, created, url, repos[10]}`
- `mac`: `{oui, vendor, local, notes}`. Only the OUI (first three octets) is sent upstream; locally
  administered (randomised) addresses are answered without a request.

Other: `weather` item `url` is the NWS API URL of the alert (NWS has no HTML page per alert). `natural`
items have `country: null` (EONET gives none). EONET is queried with `limit=1500`, GDACS for the last 7 days.

## Traffic cameras (added after 0.1.0)

Layer `cameras` (label "Traffic cameras", refresh 1 h): official open-data cameras only.

| Source | List | Images | Licence |
|---|---|---|---|
| Transport for London JamCams | `api.tfl.gov.uk/Place/Type/JamCam` | `s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/` | Powered by TfL Open Data |
| Singapore LTA traffic images | `api.data.gov.sg/v1/transport/traffic-images` | `images.data.gov.sg/` | Singapore Open Data Licence v1.0 |

Item ids: `cameras:tfl:{id}`, `cameras:sg:{id}`; kind `traffic`; no `time` for TfL. Image URLs are not sent to the page.

`GET /camera/{id}` → `{"ok": true, "id", "image": "data:image/jpeg;base64,…", "captured": ms | null, "fetched": ms, "attribution"}`.
Only ids the layer has listed are served (404 otherwise); the image URL must be https on one of the two hosts
and path prefixes above; the body is capped at 1.5 MB and must be a JPEG, PNG or WebP by signature
(data.gov.sg labels its JPEGs `application/octet-stream`). Frames are cached for 60 s.

The page clusters cameras within 150 km into one marker until the camera is closer than 1.1 globe radii.
