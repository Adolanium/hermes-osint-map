"""Layer registry, part combination, parameter validation and failure handling."""

import pytest

from osintmap import layers
from osintmap.http import SnapshotCache, UpstreamError


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(layers, "_cache", SnapshotCache())
    monkeypatch.setattr(layers, "_area_cache", SnapshotCache())  # parts read the module global per call
    layers.reset()
    yield
    layers.reset()


def snap(items=None, status="ok", error=None, updated=1, **extra):
    value = None if items is None else {"items": items, **extra}
    return {"value": value, "status": status, "error": error, "updated": updated if value is not None else None}


def entry(key, severity=0.5):
    return {"id": key, "severity": severity}


def test_combine_dedupes_with_earlier_parts_winning():
    a = snap([{"id": "x", "severity": 0.1, "from": "a"}])
    b = snap([{"id": "x", "severity": 0.9, "from": "b"}, entry("y")])
    result = layers.combine([a, b], cap=10)
    assert [i["id"] for i in result["items"]] == ["x", "y"] and result["items"][0]["from"] == "a"
    assert result["status"] == "ok" and result["error"] is None


def test_combine_status_and_extras():
    ok, failed = snap([entry("a")], updated=5, dropped=2), snap(None, status="error", error="GDACS is down.")
    assert layers.combine([ok, failed], 10)["status"] == "stale"
    assert layers.combine([failed], 10) == {"status": "error", "error": "GDACS is down.", "updated": None, "items": []}
    assert layers.combine([ok, snap([], dropped=3)], 10)["dropped"] == 5


def test_combine_caps_by_severity():
    result = layers.combine([snap([entry(str(i), i / 10) for i in range(10)])], cap=3)
    assert [i["id"] for i in result["items"]] == ["9", "8", "7"]


def test_registry_is_idle_before_any_fetch_and_lists_every_layer():
    rows = layers.registry()
    assert [r["id"] for r in rows] == ["quakes", "natural", "disasters", "weather", "aircraft", "satellites",
                                       "incidents", "cameras", "chokepoints"]
    for row in rows:
        assert row["status"] == "idle" and row["count"] == 0 and row["needs_key"] is None
        assert {"label", "refresh_s", "source", "source_url", "attribution", "error", "updated"} <= set(row)


def test_upstream_failure_is_a_status_not_an_exception(monkeypatch):
    def down(url, **kwargs):
        raise UpstreamError(503, f"{kwargs['source']} is having trouble (HTTP 503).")

    monkeypatch.setattr(layers.http, "get_json", down)
    answer = layers.fetch("quakes")
    assert answer["status"] == "error" and answer["items"] == [] and "USGS" in answer["error"]
    assert next(r for r in layers.registry() if r["id"] == "quakes")["status"] == "error"


def test_one_feed_down_keeps_the_other(monkeypatch, fixture):
    day = fixture("usgs_day.json")

    def partial(url, **kwargs):
        if url == layers.USGS_DAY:
            return day
        raise UpstreamError(503, "USGS is having trouble (HTTP 503).")

    monkeypatch.setattr(layers.http, "get_json", partial)
    answer = layers.fetch("quakes")
    assert answer["status"] == "stale" and answer["count"] == 3 and answer["id"] == "quakes"


def test_weather_reports_dropped_zone_alerts(monkeypatch, fixture):
    monkeypatch.setattr(layers.http, "get_json", lambda url, **k: fixture("nws.json"))
    answer = layers.fetch("weather")
    assert answer["count"] == 2 and answer["dropped"] == 1


def test_chokepoints_need_no_network(monkeypatch):
    monkeypatch.setattr(layers.http, "get_json", lambda *a, **k: pytest.fail("network"))
    assert layers.fetch("chokepoints")["count"] == 15


def test_unknown_layer():
    with pytest.raises(UpstreamError) as err:
        layers.fetch("ufos")
    assert err.value.status == 404


@pytest.mark.parametrize("query,message", [({"lat": "91", "lon": "0"}, "lat must be"), ({"lat": "x", "lon": "0"}, "lat must be"),
                                           ({"lat": "10"}, "both lat and lon"), ({"lat": "1", "lon": "1", "radius": "far"}, "radius")])
def test_aircraft_params_are_validated(query, message):
    with pytest.raises(UpstreamError) as err:
        layers.fetch("aircraft", query)
    assert err.value.status == 400 and message in str(err.value)


def test_aircraft_area_query_is_clamped_rounded_and_merged(monkeypatch, fixture):
    seen = []

    def fake(url, **kwargs):
        seen.append(url)
        return fixture("adsb_mil.json") if url == layers.ADSB_MIL else fixture("adsb_point.json")

    monkeypatch.setattr(layers.http, "get_json", fake)
    answer = layers.fetch("aircraft", {"lat": "51.4712", "lon": "-0.4543", "radius": "900"})
    assert seen[1] == "https://api.adsb.lol/v2/point/51.47/-0.45/250"
    kinds = {i["kind"] for i in answer["items"]}
    assert kinds == {"military", "civil"} and answer["status"] == "ok"
    layers.fetch("aircraft", {"lat": "51.4712", "lon": "-0.4543", "radius": "900"})
    assert len(seen) == 2  # both parts served from cache the second time


def test_satellites_cap_and_groups(monkeypatch, fixture):
    stations = fixture("celestrak_stations.json")
    monkeypatch.setattr(layers.http, "get_json", lambda url, **k: stations if k["params"]["GROUP"] == "stations" else [])
    answer = layers.fetch("satellites")
    assert answer["count"] == 3 and all(i["kind"] == "stations" and "omm" in i for i in answer["items"])
