"""Dashboard routes: always HTTP 200 JSON, failures as ok:false, never an exception."""

import importlib.util
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def api():
    spec = importlib.util.spec_from_file_location("osint_test_api", ROOT / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(api):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/hermes-osint-map")
    return TestClient(app)


def get(client, path):
    response = client.get("/api/plugins/hermes-osint-map" + path)
    assert response.status_code == 200
    return response.json()


def test_layers_route_is_cheap_and_ok(client):
    body = get(client, "/layers")
    assert body["ok"] is True and len(body["layers"]) == 9


def test_bad_input_is_ok_false_with_a_sentence(client):
    for path, status in (("/layer/ufos", 404), ("/layer/aircraft?lat=999&lon=0", 400), ("/country/123", 400),
                         ("/recon/dns?q=localhost", 400), ("/recon/nmap?q=example.com", 404), ("/recon/dns", 400),
                         ("/recon/ip?q=192.168.0.1", 400), ("/recon/headers?q=http://127.0.0.1:8080/", 403)):
        body = get(client, path)
        assert body["ok"] is False and body["status"] == status and body["error"], path


def test_upstream_errors_and_crashes_become_ok_false(api, client, monkeypatch):
    monkeypatch.setattr(api.panels, "space_weather", lambda: (_ for _ in ()).throw(api.net.UpstreamError(503, "NOAA SWPC is down.")))
    assert get(client, "/space-weather") == {"ok": False, "error": "NOAA SWPC is down.", "status": 503}
    monkeypatch.setattr(api.panels, "markets", lambda: 1 / 0)
    body = get(client, "/markets")
    assert body["ok"] is False and body["status"] == 500 and "ZeroDivisionError" in body["error"]


def test_layer_failure_is_ok_true_with_error_status(api, client, monkeypatch):
    monkeypatch.setattr(api.layers, "_cache", api.net.SnapshotCache())
    monkeypatch.setattr(api.layers.http, "get_json",
                        lambda url, **k: (_ for _ in ()).throw(api.net.UpstreamError(503, "GDACS is having trouble (HTTP 503).")))
    body = get(client, "/layer/disasters")
    assert body["ok"] is True and body["status"] == "error" and body["items"] == [] and "GDACS" in body["error"]


def test_country_and_news_shapes(api, client, monkeypatch, fixture):
    monkeypatch.setattr(api.panels, "_country_cache", api.net.TTLCache(60))
    monkeypatch.setattr(api.panels.http, "get_json",
                        lambda url, **k: fixture("wb_indicators.json") if "indicator" in url else fixture("wb_country.json"))
    body = get(client, "/country/jp")
    assert body["ok"] is True
    assert {"code", "name", "capital", "region", "income", "lat", "lon", "population", "gdp_usd",
            "gdp_per_capita_usd", "year"} <= set(body)
    assert body["code"] == "JP"
