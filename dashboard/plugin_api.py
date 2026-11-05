"""Routes for the OSINT Map Desktop page under /api/plugins/hermes-osint-map/.

Every answer is HTTP 200 JSON, ``{"ok": true, ...}`` or ``{"ok": false, "error": ..., "status": ...}``,
so the page handles failures in one place. Handlers are plain ``def``: FastAPI runs them in its
threadpool, so the blocking upstream requests stay off the event loop. Query parameters arrive as
strings and are validated by the backend, which answers a sentence instead of FastAPI's 422.
"""

import importlib
import importlib.util
import logging
import sys
from pathlib import Path

from fastapi import APIRouter

# Load the plugin package under a private name: the agent loader's module name is its own business.
_ROOT = Path(__file__).resolve().parents[1]
_NAME = "_hermes_osint_map_http"
if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_NAME, _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)])
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _module
    _spec.loader.exec_module(_module)
net = importlib.import_module(_NAME + ".osintmap.http")
layers = importlib.import_module(_NAME + ".osintmap.layers")
panels = importlib.import_module(_NAME + ".osintmap.panels")
recon = importlib.import_module(_NAME + ".osintmap.recon")
cameras = importlib.import_module(_NAME + ".osintmap.cameras")
areas = importlib.import_module(_NAME + ".osintmap.area")
watches = importlib.import_module(_NAME + ".osintmap.watch")
scenes = importlib.import_module(_NAME + ".osintmap.show")
cyberfeed = importlib.import_module(_NAME + ".osintmap.cyberfeed")
checkpoint = importlib.import_module(_NAME + ".osintmap.checkpoint")

logger = logging.getLogger(__name__)
router = APIRouter()
# The Cyber attacks layer: one shared upstream stream, opened only while a page polls /cyber/events.
cyber_hub = cyberfeed.FeedHub(checkpoint.open_feed)


def _answer(work):
    try:
        return {"ok": True, **work()}
    except net.UpstreamError as exc:
        return {"ok": False, "error": str(exc), "status": exc.status}
    except Exception as exc:  # never let a route raise: the page expects JSON either way
        logger.warning("osint-map: route failed (%s)", type(exc).__name__)
        return {"ok": False, "error": net.describe(exc, "OSINT Map"), "status": 500}


@router.get("/layers")
def layer_list():
    """Registry and health; never calls an upstream."""
    return _answer(lambda: {"layers": layers.registry()})


@router.get("/layer/{layer_id}")
def layer(layer_id: str, lat: str | None = None, lon: str | None = None, radius: str | None = None):
    query = {k: v for k, v in (("lat", lat), ("lon", lon), ("radius", radius)) if v is not None}
    return _answer(lambda: layers.fetch(layer_id, query))


@router.get("/news")
def news(q: str | None = None, limit: str | None = None):
    return _answer(lambda: panels.news(q, limit))


@router.get("/space-weather")
def space_weather():
    return _answer(panels.space_weather)


@router.get("/markets")
def markets():
    return _answer(panels.markets)


@router.get("/country/{code}")
def country(code: str):
    return _answer(lambda: panels.country(code))


@router.get("/camera/{camera_id}")
def camera_frame(camera_id: str):
    """Latest still of a camera the cameras layer listed (by id only, never by URL)."""
    return _answer(lambda: cameras.frame(camera_id))


@router.get("/recon/{kind}")
def recon_lookup(kind: str, q: str | None = None):
    return _answer(lambda: recon.run(kind, q))


@router.get("/area")
def area(lat: str | None = None, lon: str | None = None, radius: str | None = None, country: str | None = None,
         layers: str | None = None, limit: str | None = None):
    """Items in a country or around a point, for the chat map card. Same filter as osint_events."""
    def work():
        where = areas.parse({"lat": lat, "lon": lon, "radius_km": radius, "country": country, "layers": layers})
        if not (where.country or where.has_point):
            raise net.UpstreamError(400, "Give a country code, or lat and lon.")
        found, answers = areas.collect(where)
        try:
            cap = max(1, min(200, int(limit or 60)))
        except ValueError:
            cap = 60
        counts = {}
        for entry in found:
            counts[entry["layer"]] = counts.get(entry["layer"], 0) + 1
        return {"area": {"lat": where.lat, "lon": where.lon, "radius_km": where.radius_km, "country": where.country,
                         "layers": list(where.layer_ids), "label": where.describe()},
                "count": len(found), "counts": counts, "items": found[:cap],
                "status": {i: {"status": a["status"], "error": a["error"], "updated": a["updated"]} for i, a in answers.items()}}
    return _answer(work)


def _plugin_state():
    """This plugin's Hermes state, the same store osint_watch writes (backend code may import Hermes)."""
    from hermes_cli.plugins_state import PluginState
    return PluginState("hermes-osint-map")


@router.get("/show")
def show_latest(after: str | None = None):
    """The newest scene Hermes put on the map with osint_show (the page polls this)."""
    def work():
        try:
            cursor = max(0, int(after or 0))
        except ValueError:
            raise net.UpstreamError(400, "after must be a whole number.") from None
        return scenes.latest(_plugin_state(), cursor)
    return _answer(work)


@router.get("/watches")
def watch_list():
    return _answer(lambda: {"watches": watches.listing(_plugin_state())})


@router.post("/watches/{watch_id}/forget")
def watch_forget(watch_id: str):
    return _answer(lambda: {"forgotten": watches.forget(_plugin_state(), watch_id)})


@router.get("/watches/prompt")
def watch_prompt(watch_id: str, label: str, lat: str | None = None, lon: str | None = None, radius: str | None = None,
                 country: str | None = None, layers: str | None = None, min_magnitude: str | None = None,
                 min_alert_level: str | None = None):
    """The cron prompt for a new watch, validated here so a bad area fails before a job exists."""
    def work():
        args = {"lat": lat, "lon": lon, "radius_km": radius, "country": country, "layers": layers,
                "min_magnitude": min_magnitude, "min_alert_level": min_alert_level}
        where = areas.parse(args, default_layers=list(watches.DEFAULT_LAYERS))
        if not (where.country or where.has_point):
            raise net.UpstreamError(400, "A watch needs a country, or lat and lon.")
        if not watches.WATCH_ID.match(watch_id or ""):
            raise net.UpstreamError(400, "That watch id is not valid.")
        watches.rules_from(args)
        clean = {k: v for k, v in {"country": where.country, "lat": where.lat, "lon": where.lon,
                                   "radius_km": where.radius_km if where.has_point else None,
                                   "min_magnitude": min_magnitude, "min_alert_level": min_alert_level}.items()
                 if v not in (None, "")}
        clean["layers"] = list(where.layer_ids)
        label_clean = (label or where.describe()).replace('"', "").strip()[:60]
        return {"prompt": watches.job_prompt(watch_id, clean, label_clean), "label": label_clean}
    return _answer(work)


@router.get("/cyber/events")
def cyber_events(after: str | None = None, limit: str | None = None):
    """Attacks newer than ``after`` from the Cyber attacks layer. Polling keeps the stream open; it
    closes by itself shortly after the last poll."""
    def work():
        try:
            cursor = max(0, int(after or 0))
            count = max(1, min(500, int(limit or 500)))
        except ValueError:
            raise net.UpstreamError(400, "after and limit must be whole numbers.") from None
        return cyber_hub.poll(cursor, count)
    return _answer(work)


@router.post("/cyber/stop")
def cyber_stop():
    cyber_hub.stop()
    return _answer(cyber_hub.status)


@router.get("/cyber/top")
def cyber_top():
    return _answer(checkpoint.top_stats)
