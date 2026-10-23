"""Agent tools: registration, filters, and handlers that always answer JSON."""

import importlib.util
import json
from pathlib import Path

import pytest

from osintmap import layers, panels, tools
from osintmap.http import UpstreamError

ROOT = Path(__file__).resolve().parents[1]


def item(key, layer="quakes", country=None, lat=10.0, lon=10.0, severity=0.5):
    return {"id": f"{layer}:{key}", "layer": layer, "kind": "k", "lat": lat, "lon": lon, "title": f"Item {key}",
            "subtitle": None, "severity": severity, "time": None, "country": country, "url": None}


@pytest.fixture
def fake_layers(monkeypatch):
    data = {
        "quakes": [item("jp", country="JP", lat=35.6, lon=139.7, severity=0.9), item("cl", country="CL", lat=-33, lon=-70)],
        "incidents": [item("kyiv", "incidents", "UA", 50.45, 30.52, 0.8), item("tokyo", "incidents", "JP", 35.7, 139.8, 0.4)],
    }
    calls = []

    def fetch(layer_id, query=None):
        calls.append((layer_id, query))
        items = data.get(layer_id, [])
        return {"id": layer_id, "status": "ok", "error": None, "updated": 1, "count": len(items), "items": items}

    monkeypatch.setattr(layers, "fetch", fetch)
    return calls


def run(name, args=None):
    return json.loads(tools.HANDLERS[name](args or {}))


def test_register_declares_every_manifest_tool():
    spec = importlib.util.spec_from_file_location("hermes_osint_map_pkg", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    package = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = package
    spec.loader.exec_module(package)

    class Ctx:
        def __init__(self):
            self.tools = []

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)

    ctx = Ctx()
    package.register(ctx)
    manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    names = [t["name"] for t in ctx.tools]
    assert names == ["osint_situation", "osint_events", "osint_country", "osint_recon", "osint_news"]
    for tool in ctx.tools:
        assert f"  - {tool['name']}" in manifest
        assert tool["toolset"] == "osint_map" and tool["schema"]["name"] == tool["name"] and callable(tool["handler"])


def test_events_filters_by_country_and_radius(fake_layers):
    body = run("osint_events", {"country": "jp"})
    assert body["ok"] and body["count"] == 2 and "Item jp" in body["text"] and "Item kyiv" not in body["text"]
    body = run("osint_events", {"lat": 50.4, "lon": 30.5, "radius_km": 50})
    assert body["count"] == 1 and "Item kyiv" in body["text"]
    aircraft = [q for layer_id, q in fake_layers if layer_id == "aircraft"]
    assert aircraft[-1] == {"lat": 50.4, "lon": 30.5, "radius": pytest.approx(50 / 1.852)}


def test_events_bad_arguments_answer_errors(fake_layers):
    assert run("osint_events", {"layer": "ufos"})["ok"] is False
    assert run("osint_events", {"country": "Japan"})["ok"] is False
    assert run("osint_events", {"lat": 10})["ok"] is False


def test_country_brief_survives_missing_sources(fake_layers, monkeypatch):
    monkeypatch.setattr(panels, "country", lambda code: (_ for _ in ()).throw(UpstreamError(503, "World Bank down.")))
    monkeypatch.setattr(panels, "news", lambda q=None, limit=None: {"items": [{"title": "Tokyo story", "source": "BBC World",
                                                                                 "time": None, "url": "https://x"}]})
    body = run("osint_country", {"code": "JP"})
    assert body["ok"] and "World Bank facts unavailable" in body["text"] and "Item jp" in body["text"]
    assert "Tokyo story" in body["text"] and "Item kyiv" not in body["text"]


def test_handlers_never_raise(monkeypatch):
    monkeypatch.setattr(layers, "fetch_many", lambda *a, **k: 1 / 0)
    body = run("osint_situation")
    assert body == {"ok": False, "error": "OSINT Map failed unexpectedly (ZeroDivisionError)."}
    assert run("osint_recon", {"kind": "dns", "target": "localhost"})["ok"] is False
    assert run("osint_recon", {})["ok"] is False
    assert json.loads(tools.HANDLERS["osint_news"](None))  # missing args dict


def test_recon_tool_text(monkeypatch):
    monkeypatch.setattr(tools.recon, "run", lambda kind, target: {"kind": kind, "target": target,
                                                                   "summary": [["A", "93.184.215.14"]], "result": {},
                                                                   "sources": ["Google Public DNS (dns.google)"]})
    body = run("osint_recon", {"kind": "dns", "target": "example.com"})
    assert "- A: 93.184.215.14" in body["text"] and "Google Public DNS" in body["text"]


def test_schemas_are_well_formed():
    for name, schema in tools.SCHEMAS.items():
        assert schema["name"] == name and schema["parameters"]["type"] == "object"
        assert set(schema["parameters"]["required"]) <= set(schema["parameters"]["properties"])
