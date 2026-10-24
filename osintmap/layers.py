"""The map's layer registry: where each layer comes from, how often it refreshes, and its health.

A layer is one or more *parts*, each a single upstream request with its own cache entry (USGS has
two feeds, aircraft has the military feed plus an optional area query, satellites has one request
per CelesTrak group). Parts fail independently: if one feed is down, the others still draw and
the layer reports ``stale`` with the failing feed's sentence instead of going dark.

Nothing is fetched until the page or a tool asks. ``registry()`` only reports what earlier fetches
left behind, so ``GET /layers`` never touches the network.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import cameras, chokepoints, gdelt, http, parsers
from .http import SnapshotCache, UpstreamError

USGS_DAY = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
USGS_WEEK = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"
EONET = "https://eonet.gsfc.nasa.gov/api/v3/events"
EONET_DAYS = 30
GDACS = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
NWS = "https://api.weather.gov/alerts/active"
ADSB_MIL = "https://api.adsb.lol/v2/mil"
ADSB_POINT = "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}"
CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"
SAT_GROUPS = ("stations", "gps-ops", "weather", "military")

MAX_ITEMS = 5000
MAX_AIRCRAFT = 3000
MAX_SATELLITES = 400
MAX_RADIUS_NM = 250
DEFAULT_RADIUS_NM = 100


@dataclass(frozen=True)
class Part:
    key: str
    ttl: float
    source: str
    load: Callable[[], dict]  # → {"items": [...], optional int extras such as "dropped"}
    cache: SnapshotCache


@dataclass(frozen=True)
class Layer:
    id: str
    label: str
    source: str
    source_url: str
    attribution: str
    refresh_s: int
    parts: Callable[[dict], list[Part]]
    params: Callable[[dict], dict] = field(default=lambda query: {})
    cap: int = MAX_ITEMS
    needs_key: str | None = None


_cache = SnapshotCache(size=64)
_area_cache = SnapshotCache(size=16)  # aircraft area queries: few, short-lived, potentially large
_health: dict[str, dict] = {}
_health_lock = threading.Lock()


def _items(value: list[dict], **extra: int) -> dict:
    return {"items": value, **extra}


# --- Loaders ---------------------------------------------------------------------------------------


def _usgs(url: str) -> Callable[[], dict]:
    return lambda: _items(parsers.parse_usgs(http.get_json(url, source="USGS")))


def _eonet() -> dict:
    # Only events observed in the last EONET_DAYS: "open" alone includes eruptions last seen months ago.
    raw = http.get_json(EONET, source="NASA EONET", params={"status": "open", "days": EONET_DAYS, "limit": 1500},
                        timeout=20.0)
    return _items(parsers.parse_eonet(raw))


def _gdacs() -> dict:
    today = datetime.now(timezone.utc).date()
    params = {"eventlist": "EQ;TC;FL;VO;DR;WF", "alertlevel": "Green;Orange;Red",
              "fromDate": (today - timedelta(days=7)).isoformat(), "toDate": (today + timedelta(days=1)).isoformat()}
    return _items(parsers.parse_gdacs(http.get_json(GDACS, source="GDACS", params=params, timeout=20.0)))


def _nws() -> dict:
    raw = http.get_json(NWS, source="the US National Weather Service", params={"severity": "Extreme,Severe"},
                        headers={"Accept": "application/geo+json"}, timeout=20.0)
    found, dropped = parsers.parse_nws(raw)
    return _items(found, dropped=dropped)


# adsb.lol rate-limits bursts. The page polls two endpoints and agent tools call the same ones, so
# every adsb.lol request in the process goes through one gate: at least ADSB_GAP_S apart, and one
# polite retry after a 429.
ADSB_GAP_S = 1.2
_adsb_gate = threading.Lock()
_adsb_last = [0.0]


def _adsb_get(url: str, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
    for attempt in range(2):
        with _adsb_gate:
            wait = _adsb_last[0] + ADSB_GAP_S - clock()
            if wait > 0:
                sleep(wait)
            _adsb_last[0] = clock()
            try:
                return http.get_json(url, source="adsb.lol")
            except UpstreamError as exc:
                if exc.status != 429 or attempt:
                    raise
        sleep(2.5)  # outside the gate, so other callers are not held up
    raise UpstreamError(429, "adsb.lol is rate limiting requests; try again in a minute.")  # pragma: no cover


def _adsb(url: str, military: bool) -> Callable[[], dict]:
    return lambda: _items(parsers.parse_adsb(_adsb_get(url), military=military))


def _celestrak(group: str) -> Callable[[], dict]:
    def load() -> dict:
        raw = http.get_json(CELESTRAK, source="CelesTrak", params={"GROUP": group, "FORMAT": "json"})
        return _items(parsers.parse_celestrak(raw, group))
    return load


def _gdelt_text(url: str) -> str:
    return http.get(url, source="GDELT", timeout=15.0).text()


def _gdelt_bytes(url: str) -> bytes:
    return http.get(url, source="GDELT", timeout=20.0, max_bytes=24 * 1024 * 1024).body


_window = gdelt.Window(_gdelt_text, _gdelt_bytes)


# --- Parameters ------------------------------------------------------------------------------------


def _float(query: dict, name: str, low: float, high: float) -> float | None:
    raw = query.get(name)
    if raw is None or raw == "":
        return None
    value = parsers.number(raw, low, high)
    if value is None:
        raise UpstreamError(400, f"{name} must be a number between {low:g} and {high:g}.")
    return value


def _aircraft_params(query: dict) -> dict:
    lat, lon = _float(query, "lat", -90, 90), _float(query, "lon", -180, 180)
    if (lat is None) != (lon is None):
        raise UpstreamError(400, "Give both lat and lon for an area query, or neither.")
    if lat is None:
        return {}
    radius = parsers.number(query.get("radius"), 0, 1e6) if query.get("radius") not in (None, "") else DEFAULT_RADIUS_NM
    if radius is None:
        raise UpstreamError(400, "radius must be a number of nautical miles.")
    # Rounded so nearby pans share a cache entry (0.01° is about 1 km).
    return {"lat": round(lat, 2), "lon": round(lon, 2), "radius": int(max(1, min(MAX_RADIUS_NM, round(radius))))}


def _aircraft_parts(params: dict) -> list[Part]:
    parts = [Part("aircraft:mil", 20, "adsb.lol", _adsb(ADSB_MIL, True), _cache)]
    if params:
        url = ADSB_POINT.format(**params)
        key = f"aircraft:point:{params['lat']}:{params['lon']}:{params['radius']}"
        parts.append(Part(key, 15, "adsb.lol", _adsb(url, False), _area_cache))
    return parts


LAYERS: dict[str, Layer] = {layer.id: layer for layer in (
    Layer("quakes", "Earthquakes", "USGS", "https://earthquake.usgs.gov", "USGS (public domain)", 60,
          lambda p: [Part("quakes:day", 60, "USGS", _usgs(USGS_DAY), _cache),
                     Part("quakes:week", 60, "USGS", _usgs(USGS_WEEK), _cache)]),
    Layer("natural", "Natural events", "NASA EONET", "https://eonet.gsfc.nasa.gov", "NASA EONET (public domain)", 600,
          lambda p: [Part("natural", 600, "NASA EONET", _eonet, _cache)]),
    Layer("disasters", "Disaster alerts", "GDACS", "https://www.gdacs.org",
          "GDACS, a cooperation of the UN and the European Commission", 600,
          lambda p: [Part("disasters", 600, "GDACS", _gdacs, _cache)]),
    Layer("weather", "Severe weather (US)", "NWS", "https://www.weather.gov", "US National Weather Service (public domain)",
          300, lambda p: [Part("weather", 300, "the US National Weather Service", _nws, _cache)]),
    Layer("aircraft", "Aircraft", "adsb.lol", "https://adsb.lol", "adsb.lol (ODbL)", 15,
          _aircraft_parts, _aircraft_params, cap=MAX_AIRCRAFT),
    Layer("satellites", "Satellites", "CelesTrak", "https://celestrak.org", "CelesTrak (Dr. T.S. Kelso)", 6 * 3600,
          lambda p: [Part(f"satellites:{g}", 6 * 3600, "CelesTrak", _celestrak(g), _cache) for g in SAT_GROUPS],
          cap=MAX_SATELLITES),
    Layer("incidents", "News incidents", "GDELT", "https://www.gdeltproject.org",
          "The GDELT Project (cite GDELT when reusing)", 900,
          lambda p: [Part("incidents", 900, "GDELT", lambda: _items(_window.refresh()), _cache)]),
    Layer("cameras", "Traffic cameras", "TfL, data.gov.sg", "https://api-portal.tfl.gov.uk",
          "Powered by TfL Open Data; Singapore traffic images from data.gov.sg (Singapore Open Data Licence)", 3600,
          lambda p: [Part("cameras:tfl", 3600, "TfL", cameras.loader(cameras.TFL, cameras.parse_tfl, "TfL"), _cache),
                     Part("cameras:sg", 3600, "data.gov.sg",
                          cameras.loader(cameras.SINGAPORE, cameras.parse_singapore, "data.gov.sg"), _cache)]),
    Layer("chokepoints", "Chokepoints", "Curated", "https://en.wikipedia.org/wiki/Choke_point",
          "Curated by this plugin from public geography", 0,
          lambda p: [Part("chokepoints", 10 ** 9, "the built-in list", lambda: _items(chokepoints.items()), _cache)]),
)}


# --- Fetching --------------------------------------------------------------------------------------


def combine(snapshots: list[dict], cap: int) -> dict:
    """Merge part snapshots into one layer answer. Items are deduplicated by id (earlier parts
    win); the layer is ``ok`` only if every part is, ``error`` only if no part has data."""
    seen: dict[str, dict] = {}
    extras: dict[str, int] = {}
    errors: list[str] = []
    for snap in snapshots:
        value = snap["value"] or {}
        for entry in value.get("items", []):
            seen.setdefault(entry["id"], entry)
        for key, extra in value.items():
            if key != "items" and isinstance(extra, int):
                extras[key] = extras.get(key, 0) + extra
        if snap["error"] and snap["error"] not in errors:
            errors.append(snap["error"])
    found = list(seen.values())
    if len(found) > cap:
        found.sort(key=lambda i: i["severity"], reverse=True)
        found = found[:cap]
    with_data = [s for s in snapshots if s["value"] is not None]
    if not errors:
        status = "ok"
    elif with_data:
        status = "stale"
    else:
        status = "error"
    updated = min((s["updated"] for s in with_data if s["updated"]), default=None)
    return {"status": status, "error": " ".join(errors) or None, "updated": updated, "items": found, **extras}


def fetch(layer_id: str, query: dict | None = None) -> dict:
    """``GET /layer/{id}``: the layer's items through its caches. Bad parameters raise
    ``UpstreamError(400)``; upstream failures never raise, they show up in ``status``."""
    layer = LAYERS.get(layer_id)
    if layer is None:
        raise UpstreamError(404, f"There is no layer called {str(layer_id)[:40]!r}.")
    params = layer.params(query or {})
    snapshots = [part.cache.fetch(part.key, part.ttl, part.load, source=part.source) for part in layer.parts(params)]
    result = combine(snapshots, layer.cap)
    with _health_lock:
        _health[layer.id] = {"status": result["status"], "error": result["error"], "updated": result["updated"],
                             "count": len(result["items"])}
    return {"id": layer.id, "count": len(result["items"]), **result}


def fetch_many(layer_ids: list[str] | None = None, workers: int = 6) -> dict[str, dict]:
    """Several layers at once (default parameters), for the agent tools."""
    ids = [i for i in (layer_ids or list(LAYERS)) if i in LAYERS]
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(ids))), thread_name_prefix="osint-layer") as pool:
        return dict(zip(ids, pool.map(fetch, ids)))


def registry() -> list[dict]:
    """``GET /layers``: static facts plus the health the last fetch left. No network."""
    out = []
    with _health_lock:
        health = {k: dict(v) for k, v in _health.items()}
    for layer in LAYERS.values():
        seen = health.get(layer.id) or {"status": "idle", "error": None, "updated": None, "count": 0}
        out.append({
            "id": layer.id, "label": layer.label, **seen, "refresh_s": layer.refresh_s,
            "source": layer.source, "source_url": layer.source_url, "attribution": layer.attribution,
            "needs_key": layer.needs_key,
        })
    return out


def reset() -> None:
    """Forget health (tests)."""
    with _health_lock:
        _health.clear()
