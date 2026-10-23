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

logger = logging.getLogger(__name__)
router = APIRouter()


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


@router.get("/recon/{kind}")
def recon_lookup(kind: str, q: str | None = None):
    return _answer(lambda: recon.run(kind, q))
